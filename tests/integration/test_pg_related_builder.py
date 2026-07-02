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

"""Postgres related-episodes builder (spec #44 port of spec #46).

Exercises the full blend against a REAL Postgres with pgvector: centroid
backfill from chunk embeddings, dense (HNSW) + lexical (tsquery) candidate
legs, TF-IDF gate, episode_related/related_idf persistence, the scoped
incremental update path, and idempotence. Embeddings are deterministic numpy
vectors inserted directly via SQL — the plumbing under test is SQL/vector
fidelity, not model relevance.
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_ok(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# sklearn (TfidfVectorizer) ships in the [entities] extra; the CI python job
# installs only dev+postgres, so skip rather than fail there.
pytest.importorskip("sklearn", reason="related-builder needs scikit-learn ([entities] extra)")

pytestmark = pytest.mark.skipif(not _pg_ok(PG_DSN), reason="Postgres not reachable — set TEST_DATABASE_URL")

DIM = 384
# Any model known to embedding_dim_for with dim 384 works; the builder only
# uses the name as a partition key + dimension lookup.
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

PODCAST_ID = str(uuid.uuid4())
EP_FIT_A = str(uuid.uuid4())  # fitness — near-identical vectors + shared vocab with B
EP_FIT_B = str(uuid.uuid4())
EP_BIZ_C = str(uuid.uuid4())  # business — orthogonal to fitness, similar to D lexically
EP_BIZ_D = str(uuid.uuid4())

_FIT_DOCS = [
    "biceps hypertrophy training with lunge cardio and deadlift progressions",
    "cardio conditioning biceps deadlift hypertrophy and lunge variations weekly",
    "deadlift form hypertrophy blocks lunge patterns cardio finisher biceps pump",
]
_BIZ_DOCS = [
    "startup valuation roadmap gtm strategy and quarterly revenue planning",
    "gtm roadmap revenue valuation pricing strategy for startup founders",
    "revenue planning valuation gtm motions roadmap reviews startup metrics",
]


def _unit(base: np.ndarray, seed: int, noise: float) -> np.ndarray:
    """Deterministic unit vector = base + small seeded perturbation."""
    rng = np.random.default_rng(seed)
    v = base + noise * rng.standard_normal(DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).astype(np.float32)


def _bases():
    """Three mutually-orthogonal unit basis directions."""
    a = np.zeros(DIM, dtype=np.float32)
    a[0] = 1.0
    b = np.zeros(DIM, dtype=np.float32)
    b[1] = 1.0
    c = np.zeros(DIM, dtype=np.float32)
    c[2] = 1.0
    return a, b, c


FIT_BASE, BIZ_BASE_C, BIZ_BASE_D = _bases()


def _seed_episode(conn, eid: str, title: str) -> None:
    conn.execute(
        """INSERT INTO episodes (id, podcast_id, external_id, title, audio_url)
           VALUES (%s, %s, %s, %s, 'https://rel.test/a.mp3')""",
        (eid, PODCAST_ID, f"ext-{eid[:8]}", title),
    )


def _seed_chunks(conn, eid: str, docs: list, base: np.ndarray, seed: int, noise: float = 0.02) -> None:
    for i, text in enumerate(docs):
        conn.execute(
            """INSERT INTO chunks (episode_id, segment_id, start_ms, end_ms, text, embedding_model, embedding)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (eid, i, i * 1000, (i + 1) * 1000, text, MODEL, _unit(base, seed + i, noise)),
        )


def _seed_mention(conn, eid: str, entity_id: str) -> None:
    conn.execute(
        """INSERT INTO entity_mentions
             (entity_id, resolution_status, episode_id, segment_id, start_ms, end_ms,
              surface_form, quote_excerpt, confidence, extractor)
           VALUES (%s, 'resolved', %s, 0, 0, 1000, %s, 'quote', 0.9, 'test')""",
        (entity_id, eid, entity_id.split(":", 1)[1]),
    )


def _rails(where: str = "") -> dict:
    """episode_id → [(related_id, rank, score), …] ordered by rank."""
    from thestill.utils.postgres_ext import as_str, connect

    rails: dict = {}
    with connect(PG_DSN) as conn:
        rows = conn.execute(
            f"SELECT episode_id, related_episode_id, rank, score FROM episode_related {where} ORDER BY episode_id, rank"
        ).fetchall()
    for r in rows:
        rails.setdefault(as_str(r["episode_id"]), []).append(
            (as_str(r["related_episode_id"]), r["rank"], r["score"])
        )
    return rails


@pytest.fixture
def seeded():
    """Clean tables + 4 episodes: two near-identical (fitness), two orthogonal (business)."""
    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.utils.postgres_ext import connect

    ensure_schema(PG_DSN)
    with connect(PG_DSN, vector=True) as conn:
        conn.execute(
            "TRUNCATE chunks, episode_vectors, episode_related, related_idf, "
            "entity_mentions, entities, episodes, podcasts CASCADE"
        )
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
            (PODCAST_ID, "https://rel.test/feed.xml", "Related Test Pod"),
        )
        for eid, title in [
            (EP_FIT_A, "Fitness Alpha"),
            (EP_FIT_B, "Fitness Beta"),
            (EP_BIZ_C, "Business Gamma"),
            (EP_BIZ_D, "Business Delta"),
        ]:
            _seed_episode(conn, eid, title)
        # Fitness pair: same base direction (cosine ≈ 1); business episodes
        # orthogonal to fitness AND to each other (vector signal ≈ 0 — their
        # relatedness must come from the lexical + entity legs).
        _seed_chunks(conn, EP_FIT_A, _FIT_DOCS, FIT_BASE, seed=1)
        _seed_chunks(conn, EP_FIT_B, _FIT_DOCS[::-1], FIT_BASE, seed=100)
        _seed_chunks(conn, EP_BIZ_C, _BIZ_DOCS, BIZ_BASE_C, seed=200)
        _seed_chunks(conn, EP_BIZ_D, _BIZ_DOCS[::-1], BIZ_BASE_D, seed=300)
        # Entity overlap: fitness episodes share a person, business a company.
        for ent, typ in [("person:arnold", "person"), ("company:acme", "company")]:
            conn.execute(
                "INSERT INTO entities (id, type, canonical_name) VALUES (%s, %s, %s)",
                (ent, typ, ent.split(":", 1)[1].title()),
            )
        _seed_mention(conn, EP_FIT_A, "person:arnold")
        _seed_mention(conn, EP_FIT_B, "person:arnold")
        _seed_mention(conn, EP_BIZ_C, "company:acme")
        _seed_mention(conn, EP_BIZ_D, "company:acme")
    return PG_DSN


def _build(**kw):
    from thestill.search.pg_related_builder import build_related_episodes

    return build_related_episodes(PG_DSN, embedding_model_name=MODEL, **kw)


def test_build_backfills_centroids_and_ranks_similar_pairs(seeded):
    from thestill.utils.postgres_ext import connect

    summary = _build()
    assert summary["episodes"] == 4
    assert summary["pairs"] >= 4

    # Centroid backfill (no episode_vectors were seeded — the builder computes
    # them from chunks.embedding).
    with connect(PG_DSN) as conn:
        cents = conn.execute("SELECT COUNT(*) AS n FROM episode_vectors WHERE embedding_model = %s", (MODEL,))
        assert cents.fetchone()["n"] == 4

    rails = _rails()
    # The two near-identical fitness episodes rank each other #1 …
    assert rails[EP_FIT_A][0][0] == EP_FIT_B
    assert rails[EP_FIT_B][0][0] == EP_FIT_A
    # … and the TF-IDF floor gates the cross-domain business episodes out of
    # the fitness rails entirely (and vice versa).
    assert {r[0] for r in rails[EP_FIT_A]} == {EP_FIT_B}
    assert {r[0] for r in rails[EP_BIZ_C]} == {EP_BIZ_D}
    assert rails[EP_BIZ_D][0][0] == EP_BIZ_C
    # Blend scores are min-max-normalised weighted sums → within [0, 1].
    for ranked in rails.values():
        for _, _, score in ranked:
            assert 0.0 <= score <= 1.0 + 1e-9


def test_build_persists_idf(seeded):
    from thestill.utils.postgres_ext import connect

    _build()
    with connect(PG_DSN) as conn:
        rows = conn.execute("SELECT term, idf FROM related_idf").fetchall()
    terms = {r["term"] for r in rows}
    assert terms, "related_idf must be persisted by the full build"
    # min_df=2 keeps only cross-document terms; both topic vocabularies qualify.
    assert "biceps" in terms and "valuation" in terms
    assert all(r["idf"] > 0 for r in rows)


def test_rebuild_is_idempotent(seeded):
    _build()
    first = _rails()
    summary = _build()
    assert _rails() == first
    assert summary["pairs"] == sum(len(v) for v in first.values())


def test_incremental_update_scopes_to_seed_and_reverse_neighbours(seeded):
    from thestill.utils.postgres_ext import connect

    _build()
    before = _rails()

    # New fitness episode using the persisted vocabulary (novel terms would be
    # IDF-invisible by design).
    new_id = str(uuid.uuid4())
    with connect(PG_DSN, vector=True) as conn:
        _seed_episode(conn, new_id, "Fitness Newcomer")
        _seed_chunks(conn, new_id, _FIT_DOCS, FIT_BASE, seed=500)
        _seed_mention(conn, new_id, "person:arnold")

    from thestill.search.pg_related_builder import update_related_for_episodes

    # candidate_cap=2 bounds each leg to 2 candidates, so the newcomer's pool
    # is the fitness pair — the business rails are reverse-out-of-scope.
    summary = update_related_for_episodes(PG_DSN, embedding_model_name=MODEL, episode_ids=[new_id], candidate_cap=2)
    assert summary["episodes"] >= 1
    after = _rails()

    # Forward: the newcomer got a rail headed by a fitness episode.
    assert new_id in after
    assert after[new_id][0][0] in {EP_FIT_A, EP_FIT_B}
    # Reverse: the fitness episodes now surface the newcomer.
    assert any(rel == new_id for rel, _, _ in after[EP_FIT_A])
    assert any(rel == new_id for rel, _, _ in after[EP_FIT_B])
    # Bounded: episodes outside the seed's candidate pool are untouched
    # (identical rows, including scores/ranks).
    assert after[EP_BIZ_C] == before[EP_BIZ_C]
    assert after[EP_BIZ_D] == before[EP_BIZ_D]

    # Re-running the same incremental update is idempotent.
    update_related_for_episodes(PG_DSN, embedding_model_name=MODEL, episode_ids=[new_id], candidate_cap=2)
    assert _rails() == after


def test_incremental_without_idf_falls_back_to_full_build(seeded):
    from thestill.search.pg_related_builder import update_related_for_episodes

    # No prior build → related_idf empty → full build fallback covers everyone.
    summary = update_related_for_episodes(PG_DSN, embedding_model_name=MODEL, episode_ids=[EP_FIT_A])
    assert summary["episodes"] == 4
    rails = _rails()
    assert set(rails) == {EP_FIT_A, EP_FIT_B, EP_BIZ_C, EP_BIZ_D}
