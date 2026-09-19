# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Spec #56 §Testing "Complexity guard" — the incremental rail update is O(k), not O(n).

Counters, not wall-clock, carry the assertion: one candidate query per
seed, episodes touched bounded by the two candidate legs, pair scores
bounded by k², and none of it moving between a 200- and a 1,000-episode
corpus. Before Phase 1 one seed touched the whole corpus (``affected=1931``
of 1,931 locally on 2026-09-16, 866 s).
"""

from __future__ import annotations

import sqlite3
import struct
import time
import uuid

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from thestill.core.embedding_model import centroid_blob
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.search.related_builder import _TFIDF_KWARGS, IncrementalStats, _persist_idf, update_related_for_episodes
from thestill.utils.sqlite_ext import maybe_load_vec_extension

DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)
TOPICS = 40
WORDS_PER_TOPIC = 12

# A synthetic vocabulary: each topic owns distinctive tokens so TF-IDF and
# the lexical leg have signal; the dense leg clusters by topic too.
_VOCAB = [[f"topic{t}word{w}" for w in range(WORDS_PER_TOPIC)] for t in range(TOPICS)]
_COMMON = ["people", "market", "growth", "system", "energy", "history", "science", "money"]


def _episode_doc(rng, topic: int) -> list[str]:
    words = list(rng.choice(_VOCAB[topic], size=7, replace=False)) + list(rng.choice(_COMMON, size=2))
    rng.shuffle(words)
    return [" ".join(words[:5]), " ".join(words[4:])]


def _unit(rng, topic: int) -> bytes:
    base = np.zeros(DIM, dtype=np.float32)
    base[topic % DIM] = 1.0
    v = base + 0.15 * rng.standard_normal(DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return struct.pack(f"<{DIM}f", *v)


def build_corpus(db_path: str, n: int, *, seed: int = 7) -> tuple[list[str], dict[str, list[str]]]:
    """Seed ``n`` episodes (2 chunks each) with centroids and a persisted IDF."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    SqlitePodcastRepository(db_path=db_path)
    rng = np.random.default_rng(seed)
    pid = str(uuid.uuid4())
    eids = [str(uuid.uuid4()) for _ in range(n)]
    docs: dict[str, list[str]] = {}
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)", (pid, "https://x/f.xml", "P", "p")
        )
        for i, eid in enumerate(eids):
            topic = i % TOPICS
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) VALUES (?, ?, ?, ?, ?, ?)",
                (eid, pid, f"ext-{i}", f"Episode {i}", f"https://x/{i}.mp3", "2026-01-01T00:00:00+00:00"),
            )
            chunks = _episode_doc(rng, topic)
            docs[eid] = chunks
            blobs = []
            for seg, text in enumerate(chunks):
                blob = _unit(rng, topic)
                blobs.append(blob)
                conn.execute(
                    "INSERT INTO chunks (episode_id, segment_id, start_ms, end_ms, speaker, text, embedding_model, embedding)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (eid, seg, seg * 1000, seg * 1000 + 900, "host", text, DEFAULT_EMBEDDING_MODEL, blob),
                )
            conn.execute(
                "INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid) VALUES (?, ?, ?, ?)",
                (eid, DEFAULT_EMBEDDING_MODEL, len(blobs), centroid_blob(blobs, DIM)),
            )
        conn.commit()
    vectorizer = TfidfVectorizer(**_TFIDF_KWARGS)
    vectorizer.fit([" ".join(docs[e]) for e in eids])
    _persist_idf(db_path, vectorizer)
    return eids, docs


def _add_episode(db_path: str, topic: int, *, seed: int) -> str:
    rng = np.random.default_rng(seed)
    eid = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        pid = conn.execute("SELECT id FROM podcasts LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, pid, f"ext-{eid[:8]}", "Newcomer", "https://x/new.mp3", "2026-02-01T00:00:00+00:00"),
        )
        blobs = []
        for seg, text in enumerate(_episode_doc(rng, topic)):
            blob = _unit(rng, topic)
            blobs.append(blob)
            conn.execute(
                "INSERT INTO chunks (episode_id, segment_id, start_ms, end_ms, speaker, text, embedding_model, embedding)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, seg, seg * 1000, seg * 1000 + 900, "host", text, DEFAULT_EMBEDDING_MODEL, blob),
            )
        conn.execute(
            "INSERT INTO episode_vectors (episode_id, embedding_model, chunk_count, centroid) VALUES (?, ?, ?, ?)",
            (eid, DEFAULT_EMBEDDING_MODEL, len(blobs), centroid_blob(blobs, DIM)),
        )
        conn.commit()
    return eid


def bounds(k: int) -> dict[str, int]:
    """Phase 1 ceilings in terms of the pool size only (two legs of k each, rails of 5)."""
    pool = 2 * k
    return {
        "candidate_queries": 1,
        "episodes_touched": pool + 1,
        "pair_scores": pool + pool * (pool + 5),
    }


def _run(db_path: str, seed_id: str, k: int) -> IncrementalStats:
    stats = IncrementalStats()
    update_related_for_episodes(
        db_path, embedding_model_name=DEFAULT_EMBEDDING_MODEL, episode_ids=[seed_id], pool_k=k, stats=stats
    )
    return stats


@pytest.mark.parametrize("n", [200, 1000])
def test_one_seed_touches_at_most_k_scale_work_regardless_of_corpus_size(tmp_path, n):
    k = 50
    db_path = str(tmp_path / f"corpus-{n}.db")
    build_corpus(db_path, n)
    seed_id = _add_episode(db_path, topic=3, seed=n)

    started = time.perf_counter()
    stats = _run(db_path, seed_id, k)
    elapsed = time.perf_counter() - started

    limit = bounds(k)
    assert stats.seeds == 1
    assert stats.candidate_queries == limit["candidate_queries"]  # one per seed, none for reverse targets
    assert stats.episodes_touched <= limit["episodes_touched"]
    assert stats.pair_scores <= limit["pair_scores"]
    # The old path touched ~the whole corpus; Phase 1 must stay well clear of n.
    assert stats.episodes_touched < n / 2
    # Loose sanity check: seconds, not minutes, at either corpus size.
    assert elapsed < 60, f"incremental update took {elapsed:.1f}s at n={n}"


def test_counters_grow_with_k_not_n(tmp_path):
    db_path = str(tmp_path / "corpus.db")
    build_corpus(db_path, 300)
    seed_id = _add_episode(db_path, topic=5, seed=1)
    small = _run(db_path, seed_id, 10)
    large = _run(db_path, seed_id, 40)
    assert small.candidate_queries == large.candidate_queries == 1
    assert small.episodes_touched <= large.episodes_touched
    assert small.pair_scores < large.pair_scores
    assert large.episodes_touched <= bounds(40)["episodes_touched"]


def test_reverse_targets_keep_a_rail_the_update_could_not_recompute(tmp_path):
    """Never delete what we didn't score: a pool member without a centroid keeps its rail."""
    db_path = str(tmp_path / "corpus.db")
    eids, _ = build_corpus(db_path, 60)
    victim = eids[3]
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)  # chunk deletes fan out into the vec0 index
        conn.execute(
            "INSERT INTO episode_related (episode_id, related_episode_id, rank, score) VALUES (?, ?, 0, 0.5)",
            (victim, eids[4]),
        )
        # No chunks and no centroid: nothing to rescore it from, and nothing
        # for the centroid backfill to rebuild it from either.
        conn.execute("DELETE FROM chunks WHERE episode_id = ?", (victim,))
        conn.execute("DELETE FROM episode_vectors WHERE episode_id = ?", (victim,))
        conn.commit()
    seed_id = _add_episode(db_path, topic=3, seed=9)
    _run(db_path, seed_id, 30)
    with sqlite3.connect(db_path) as conn:
        kept = conn.execute("SELECT related_episode_id FROM episode_related WHERE episode_id = ?", (victim,)).fetchall()
    assert kept == [(eids[4],)]
