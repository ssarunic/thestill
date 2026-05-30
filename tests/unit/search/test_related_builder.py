"""Spec #28 §5.2 — related_builder unit tests.

Seeds a tiny corpus with clearly-separated topics (two strength-training
episodes, one venture-capital episode, one isolated cooking episode) and
asserts the TF-IDF-gated blend links the on-topic pair, gates out the
cross-domain episode entirely, and respects top_n / the threshold.
"""

from __future__ import annotations

import hashlib
import sqlite3
import struct
import uuid
from typing import List

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy", reason="numpy required")
pytest.importorskip("sklearn", reason="scikit-learn required for TF-IDF")

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.search.related_builder import build_related_episodes, update_related_for_episodes

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


class _StubEmbeddingModel(EmbeddingModel):
    """Deterministic hash-keyed vectors — the dense signal is noise here;
    TF-IDF (over the real chunk text) is what the test exercises."""

    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        # Stable (non-PYTHONHASHSEED) seed so the ANN candidate order is
        # deterministic across runs — builtin hash() is randomised per process.
        seed = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(_DIM).astype(np.float32)
        v /= np.linalg.norm(v) or 1.0
        return struct.pack(f"<{_DIM}f", *v)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [self.encode_one(t) for t in texts]


# Topic-distinct chunk text. Shared terms recur across the two strength
# episodes (so TF-IDF links them) and never in the VC/cooking ones.
_STRENGTH_A = [
    "Building muscle requires progressive overload and enough protein.",
    "A heavy squat and deadlift drive strength and hypertrophy gains.",
    "Training the legs with squats protects the knees from injury.",
    "Muscle protein synthesis peaks with resistance training and protein.",
]
_STRENGTH_B = [
    "Hypertrophy comes from progressive overload across training weeks.",
    "Squat depth and protein intake matter for muscle and strength.",
    "Resistance training builds muscle and protects against injury.",
    "Protein timing supports muscle growth after a heavy training session.",
]
_VC = [
    "The venture capital fund led a Series A startup financing round.",
    "Valuations and dilution shape the cap table for founders and investors.",
    "The SaaS startup raised growth funding at a billion dollar valuation.",
    "Limited partners back the fund chasing venture returns and markups.",
]
_COOKING = [
    "Caramelizing onions slowly builds a deep savory flavor base.",
    "Whisk the eggs and butter into a silky hollandaise sauce.",
    "Roast the garlic until golden then fold it into the mashed potatoes.",
    "A pinch of saffron lifts the seafood paella with floral aroma.",
]


def _seed(tmp_path) -> tuple[str, dict]:
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    pid = str(uuid.uuid4())
    episodes = {
        "strength_a": (str(uuid.uuid4()), "Build Muscle and Strength", _STRENGTH_A),
        "strength_b": (str(uuid.uuid4()), "Hypertrophy and Protein", _STRENGTH_B),
        "vc": (str(uuid.uuid4()), "Venture Funding Deep Dive", _VC),
        "cooking": (str(uuid.uuid4()), "Mastering French Sauces", _COOKING),
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (pid, "https://example.com/p.xml", "Test Pod", "test-pod"),
        )
        for key, (eid, title, _) in episodes.items():
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, pid, f"ext-{key}", title, key.replace("_", "-"), f"https://e/{key}.mp3", "2026-01-01T00:00:00"),
            )
        conn.commit()

    writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
    for _key, (eid, _title, texts) in episodes.items():
        writer.write_episode(
            eid,
            AnnotatedTranscript(
                episode_id=eid,
                segments=[
                    AnnotatedSegment(id=i, start=float(i), end=float(i) + 1, text=t, speaker="Host", kind="content")
                    for i, t in enumerate(texts)
                ],
            ),
        )
    return db_path, {k: v[0] for k, v in episodes.items()}


def _related(db_path: str, episode_id: str) -> List[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT related_episode_id FROM episode_related WHERE episode_id=? ORDER BY rank",
                (episode_id,),
            )
        ]
    finally:
        conn.close()


# In this 4-doc corpus, TF-IDF cosine is strength↔strength ≈ 0.95 but
# cross-topic ≈ 0.27 (degenerate IDF inflates it vs the production
# 764-doc corpus, where the default floor already separates cleanly). A
# 0.5 floor reproduces production gating behaviour at test scale.
_TEST_FLOOR = 0.5


def test_links_on_topic_pair_and_gates_cross_domain(tmp_path):
    db_path, ids = _seed(tmp_path)
    summary = build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, tfidf_floor=_TEST_FLOOR)
    assert summary["pairs"] > 0

    rel = _related(db_path, ids["strength_a"])
    # The other strength episode is related; VC and cooking are gated out.
    assert ids["strength_b"] in rel
    assert ids["vc"] not in rel
    assert ids["cooking"] not in rel


def test_ranks_on_topic_episode_first(tmp_path):
    # Floor-independent: regardless of gating, the genuinely-related
    # episode must rank above cross-topic ones by the blend.
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    assert _related(db_path, ids["strength_a"])[0] == ids["strength_b"]


def test_symmetric_topical_link(tmp_path):
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    assert ids["strength_a"] in _related(db_path, ids["strength_b"])


def test_threshold_drops_weak_matches(tmp_path):
    db_path, ids = _seed(tmp_path)
    # A strict floor keeps only the strong strength↔strength link
    # (cos ≈ 0.95); every weaker cross-topic pair is dropped, so the
    # off-topic episodes surface nothing rather than padding to top_n.
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, tfidf_floor=0.9)
    assert _related(db_path, ids["strength_a"]) == [ids["strength_b"]]
    assert _related(db_path, ids["vc"]) == []
    assert _related(db_path, ids["cooking"]) == []


def test_respects_top_n(tmp_path):
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, top_n=1)
    for eid in ids.values():
        assert len(_related(db_path, eid)) <= 1


def test_rebuild_replaces_prior_rows(tmp_path):
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    first = build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    # Idempotent: a second run yields the same pair count, not double.
    with sqlite3.connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM episode_related").fetchone()[0]
    assert total == first["pairs"]


def test_candidate_cap_below_n_still_links_topical_pair(tmp_path):
    # Spec #46 Tier 2 — force the candidate-generation path (cap < N) and
    # confirm the strength pair still links via the BM25 (lexical) leg,
    # i.e. the rerank isn't only working because the pool is the whole corpus.
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, candidate_cap=2)
    assert ids["strength_b"] in _related(db_path, ids["strength_a"])


def test_persists_idf_and_episode_vec(tmp_path):
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    with sqlite3.connect(db_path) as conn:
        idf_terms = conn.execute("SELECT COUNT(*) FROM related_idf").fetchone()[0]
        vec_rows = conn.execute("SELECT COUNT(*) FROM episode_vectors").fetchone()[0]
    assert idf_terms > 0  # IDF model persisted for incremental reuse
    assert vec_rows == 4  # one centroid per episode


def _add_strength_episode(db_path: str) -> str:
    """Insert a third strength episode (chunks + centroid) and return its id."""
    eid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        pid = conn.execute("SELECT id FROM podcasts LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date) "
            "VALUES (?, ?, 'ext-sc', 'More Strength Work', 'more-strength', 'https://e/sc.mp3', '2026-02-02T00:00:00')",
            (eid, pid),
        )
        conn.commit()
    ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel()).write_episode(
        eid,
        AnnotatedTranscript(
            episode_id=eid,
            segments=[
                AnnotatedSegment(id=i, start=float(i), end=float(i) + 1, text=t, speaker="Host", kind="content")
                for i, t in enumerate(_STRENGTH_B)
            ],
        ),
    )
    return eid


def test_incremental_update_forward_and_reverse(tmp_path):
    # Spec #46 Tier 3 — add an episode after a full build, then run the
    # scoped incremental update for just that episode.
    db_path, ids = _seed(tmp_path)
    build_related_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL)
    new_id = _add_strength_episode(db_path)
    assert _related(db_path, new_id) == []  # not computed yet

    summary = update_related_for_episodes(db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, episode_ids=[new_id])
    assert summary["pairs"] > 0
    # Forward: the newcomer links to the existing strength episodes.
    forward = _related(db_path, new_id)
    assert ids["strength_a"] in forward or ids["strength_b"] in forward
    # Reverse: an existing strength episode now surfaces the newcomer,
    # without a full rebuild.
    assert new_id in _related(db_path, ids["strength_a"])


def test_incremental_falls_back_to_full_build_without_idf(tmp_path):
    # No prior full build → no related_idf → must full-build, not no-op.
    db_path, ids = _seed(tmp_path)
    summary = update_related_for_episodes(
        db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, episode_ids=[ids["strength_a"]]
    )
    assert summary["pairs"] > 0
    assert ids["strength_b"] in _related(db_path, ids["strength_a"])
