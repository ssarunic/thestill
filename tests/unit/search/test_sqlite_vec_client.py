"""Spec #28 §2.10.4 — SqliteVecBackend unit tests.

Three modes plus filter pushdown plus RRF correctness. Uses a stub
embedding model that returns deterministic vectors so semantic
matches are predictable.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from datetime import datetime
from typing import List

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy", reason="numpy required for embedding tests")

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.models.entities import EntityRecord, EntityType, MatchType
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, SearchFilters, SearchMode, embedding_dim_for
from thestill.search.sqlite_vec_client import SqliteVecBackend
from thestill.utils.sqlite_ext import maybe_load_vec_extension

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    """Deterministic L2-normalised embedding from a seed."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v) or 1.0
    return struct.pack(f"<{_DIM}f", *v)


class _StubEmbeddingModel(EmbeddingModel):
    """Returns one vector per text via the index in `texts_by_seed`.

    Lets tests control which text gets which vector so semantic
    proximity is predictable. Default seed = hash of text.
    """

    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM
        self._calls: List[str] = []

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        self._calls.append(text)
        return _vec(hash(text) % 2**31)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [self.encode_one(t) for t in texts]


def _seed_db(tmp_path) -> tuple[str, dict]:
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcasts = {
        "p1": {"id": str(uuid.uuid4()), "title": "Podcast One", "slug": "podcast-one"},
        "p2": {"id": str(uuid.uuid4()), "title": "Podcast Two", "slug": "podcast-two"},
    }
    episodes = {
        "e1": {
            "id": "11111111-2222-3333-4444-555555555555",
            "podcast_key": "p1",
            "title": "First Episode",
            "pub_date": "2026-01-15T00:00:00",
        },
        "e2": {
            "id": "22222222-3333-4444-5555-666666666666",
            "podcast_key": "p1",
            "title": "Second Episode",
            "pub_date": "2026-03-20T00:00:00",
        },
        "e3": {
            "id": "33333333-4444-5555-6666-777777777777",
            "podcast_key": "p2",
            "title": "Other Show Episode",
            "pub_date": "2026-02-10T00:00:00",
        },
    }
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        for key, p in podcasts.items():
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (p["id"], f"https://example.com/{key}.xml", p["title"], p["slug"]),
            )
        for ek, e in episodes.items():
            conn.execute(
                """
                INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    e["id"],
                    podcasts[e["podcast_key"]]["id"],
                    f"ext-{ek}",
                    e["title"],
                    f"https://example.com/{ek}.mp3",
                    e["pub_date"],
                ),
            )
        conn.commit()
    return db_path, {"podcasts": podcasts, "episodes": episodes}


def _populate_chunks(db_path: str, episode_id: str, segments: List[tuple]):
    """``segments`` is list of (seg_id, start_s, end_s, text, speaker)."""
    writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
    transcript = AnnotatedTranscript(
        episode_id=episode_id,
        segments=[
            AnnotatedSegment(id=sid, start=s, end=e, text=t, speaker=spk, kind="content")
            for sid, s, e, t, spk in segments
        ],
    )
    writer.write_episode(episode_id, transcript)


class TestLexicalMode:
    def test_returns_hit_for_term_match(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "Talking about agentic engineering today.", "Host"),
                (1, 5.0, 10.0, "Cooking dinner is fun tonight.", "Host"),
            ],
        )
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search("agentic", mode=SearchMode.LEXICAL, limit=10, filters=None)
        assert len(hits) == 1
        assert hits[0].segment_id == 0
        assert hits[0].match_type == MatchType.LEXICAL

    def test_no_hits_for_unknown_term(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "Just some text about nothing much.", "Host")])
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        assert backend.search("nonexistent", mode=SearchMode.LEXICAL, limit=10, filters=None) == []

    def test_metadata_joined_in_hit(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "agentic stuff for the whole hour", "Host")])
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hit = backend.search("agentic", mode=SearchMode.LEXICAL, limit=10, filters=None)[0]
        assert hit.episode_title == "First Episode"
        assert hit.podcast_title == "Podcast One"
        assert hit.published_at == datetime(2026, 1, 15)
        assert hit.start_ms == 1000
        assert hit.end_ms == 5000


class TestSemanticMode:
    def test_returns_nearest_neighbour(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "alpha text about quantum lasers", "Host"),
                (1, 5.0, 10.0, "beta text about sourdough baking", "Host"),
            ],
        )
        # Backend reuses the stub for query encoding → same hash, same
        # vector as the "alpha text" segment, so it wins the k-NN.
        # The "beta text" chunk gets a hash-orthogonal vector that's
        # well past the noise threshold; it's correctly dropped.
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search("Host: alpha text about quantum lasers", mode=SearchMode.SEMANTIC, limit=2, filters=None)
        assert len(hits) == 1
        assert hits[0].segment_id == 0
        assert hits[0].match_type == MatchType.SEMANTIC

    def test_drops_high_distance_noise(self, tmp_path):
        """Sarah-Palin scenario: the kNN returns hits but they're all
        past the noise threshold, so we surface zero rather than
        rendering hallucinated matches."""
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "completely unrelated chunk one", "Host"),
                (1, 5.0, 10.0, "completely unrelated chunk two", "Host"),
            ],
        )
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search("query with no relevant chunk", mode=SearchMode.SEMANTIC, limit=5, filters=None)
        assert hits == []


class TestHybridMode:
    def test_fuses_lexical_and_semantic_ranks(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "agentic engineering rocks the house", "Host"),
                (1, 5.0, 10.0, "unrelated topic chatter for a while", "Host"),
                (2, 10.0, 15.0, "agentic systems are wild", "Host"),
            ],
        )
        # Hybrid leg seeds both BM25 ("agentic" matches segs 0 and 2)
        # and the k-NN, where the stub's hash-keyed vectors give us a
        # deterministic ordering.
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search("agentic", mode=SearchMode.HYBRID, limit=3, filters=None)
        assert all(h.match_type == MatchType.HYBRID for h in hits)
        seg_ids = {h.segment_id for h in hits}
        # Both lex-matching segments (0 and 2) make the top-3.
        assert 0 in seg_ids
        assert 2 in seg_ids


class TestFilters:
    def test_podcast_id_filter(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]  # podcast p1
        e3 = fixtures["episodes"]["e3"]["id"]  # podcast p2
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "shared term here in both shows", "Host")])
        _populate_chunks(db_path, e3, [(0, 1.0, 5.0, "shared term here in both shows", "Host")])

        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        # No filter → both podcasts
        all_hits = backend.search("shared", mode=SearchMode.LEXICAL, limit=10, filters=None)
        assert len(all_hits) == 2

        # Filter to p1 only
        p1_id = fixtures["podcasts"]["p1"]["id"]
        filtered = backend.search(
            "shared",
            mode=SearchMode.LEXICAL,
            limit=10,
            filters=SearchFilters(podcast_id=p1_id),
        )
        assert len(filtered) == 1
        assert filtered[0].podcast_id == p1_id

    def test_date_range_filter(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]  # 2026-01-15
        e2 = fixtures["episodes"]["e2"]["id"]  # 2026-03-20
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "matchword in a longer sentence", "Host")])
        _populate_chunks(db_path, e2, [(0, 1.0, 5.0, "matchword in a longer sentence", "Host")])
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search(
            "matchword",
            mode=SearchMode.LEXICAL,
            limit=10,
            filters=SearchFilters(date_from="2026-02-01", date_to="2026-12-31"),
        )
        assert len(hits) == 1
        assert hits[0].episode_id == e2

    def test_has_entity_filter(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        e2 = fixtures["episodes"]["e2"]["id"]
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "matchword here in a longer sentence", "Host")])
        _populate_chunks(db_path, e2, [(0, 1.0, 5.0, "matchword here in a longer sentence", "Host")])
        # Only e1 mentions person:elon-musk
        repo = SqliteEntityRepository(db_path=db_path)
        repo.upsert_entity(EntityRecord(id="person:elon-musk", type=EntityType.PERSON, canonical_name="Elon Musk"))
        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            conn.execute(
                """
                INSERT INTO entity_mentions
                  (entity_id, resolution_status, episode_id, segment_id, start_ms, end_ms,
                   surface_form, quote_excerpt, confidence, extractor)
                VALUES ('person:elon-musk', 'resolved', ?, 0, 0, 1000, 'Musk', 'sample', 1.0, 'test')
                """,
                (e1,),
            )
            conn.commit()

        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search(
            "matchword",
            mode=SearchMode.LEXICAL,
            limit=10,
            filters=SearchFilters(has_entity=("person:elon-musk",)),
        )
        assert len(hits) == 1
        assert hits[0].episode_id == e1


class TestSearchModeDispatch:
    def test_unknown_mode_raises(self, tmp_path):
        db_path, _ = _seed_db(tmp_path)
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        with pytest.raises(ValueError, match="unknown SearchMode"):
            backend.search("x", mode="bogus", limit=1, filters=None)  # type: ignore[arg-type]


class TestShortChunkGuards:
    """2026-09-29 Legora fix: legacy one-word rows stay out of the k-NN leg, and
    hybrid fusion only admits semantic-only rows that are genuinely close."""

    def test_semantic_leg_skips_legacy_short_rows(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "a proper sentence about lasers", "Host")])
        # A row the writer would refuse today, inserted the way pre-fix writers did.
        stub = _StubEmbeddingModel()
        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            conn.execute(
                """INSERT INTO chunks (episode_id, segment_id, start_ms, end_ms, speaker, text, embedding_model, embedding)
                   VALUES (?, 99, 0, 1000, 'Host', 'Host: Ew.', ?, ?)""",
                (e1, DEFAULT_EMBEDDING_MODEL, stub.encode_one("Host: Ew.")),
            )
            conn.commit()
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        # The query encodes to exactly the legacy row's vector (distance 0) — still excluded.
        assert backend.search("Host: Ew.", mode=SearchMode.SEMANTIC, limit=5, filters=None) == []
        hits = backend.search("Host: a proper sentence about lasers", mode=SearchMode.SEMANTIC, limit=5, filters=None)
        assert [h.segment_id for h in hits] == [0]

    def test_hybrid_drops_far_semantic_only_rows(self, tmp_path, monkeypatch):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "Legora is the platform lawyers use", "Host"),
                (1, 5.0, 9.0, "the weather was lovely today", "Host"),
            ],
        )
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            cid = conn.execute("SELECT id FROM chunks WHERE segment_id = 1").fetchone()[0]
        fake = {
            "chunk_id": cid,
            "episode_id": e1,
            "segment_id": 1,
            "start_ms": 5000,
            "end_ms": 9000,
            "speaker": "Host",
            "text": "Host: the weather was lovely today",
            "episode_title": "First Episode",
            "pub_date": "2026-01-15T00:00:00",
            "podcast_id": fixtures["podcasts"]["p1"]["id"],
            "podcast_title": "Podcast One",
            "score": 0.62,
        }
        monkeypatch.setattr(backend, "_semantic", lambda *a, **k: [fake])
        hits = backend.search("Legora", mode=SearchMode.HYBRID, limit=5, filters=None)
        assert [h.segment_id for h in hits] == [0]
        fake["score"] = 0.35
        hits = backend.search("Legora", mode=SearchMode.HYBRID, limit=5, filters=None)
        assert {h.segment_id for h in hits} == {0, 1}
