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

"""pgvector search backend + Postgres chunk writer (spec #44 Phase 4).

Exercises the full write→index→search path against a REAL Postgres with the
pgvector extension: chunk insert (vector + generated tsvector), HNSW k-NN,
websearch lexical ranking, RRF hybrid, filters, idempotence, and the FM-7
sanitizer. Uses a deterministic fake embedding model — the plumbing under
test is SQL/vector fidelity, not model relevance (that is validated against
promoted production data in the E2E suite).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

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


pytestmark = pytest.mark.skipif(not _pg_ok(PG_DSN), reason="Postgres not reachable — set TEST_DATABASE_URL")

DIM = 384


class FakeEmbeddingModel:
    """Deterministic embeddings: unit vector seeded by the text's topic word.

    Texts sharing a topic word embed identically, so cosine distance is 0 for
    same-topic and ~sqrt(2) for different topics — enough to assert ranking.
    """

    model_name = "fake-384"
    dim = DIM

    def _vec(self, text: str) -> np.ndarray:
        topic = text.split()[-1].lower().strip(".")
        seed = int.from_bytes(hashlib.md5(topic.encode()).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(DIM).astype(np.float32)
        return v / np.linalg.norm(v)

    def encode_one(self, text: str) -> bytes:
        return self._vec(text).tobytes()

    def encode_batch(self, texts, *, batch_size: int = 64):
        return [self.encode_one(t) for t in texts]


class _Seg:
    def __init__(self, seg_id, text, speaker=None, kind="content", start=0.0, end=10.0):
        self.id = seg_id
        self.text = text
        self.speaker = speaker
        self.kind = kind
        self.start = start
        self.end = end


class _Transcript:
    def __init__(self, segments):
        self.segments = segments


PODCAST_ID = str(uuid.uuid4())
EPISODE_A = str(uuid.uuid4())
EPISODE_B = str(uuid.uuid4())


@pytest.fixture
def seeded(request):
    """Clean tables + two episodes with topic-distinct chunks."""
    import psycopg

    from thestill.core.postgres_chunk_writer import PostgresChunkWriter
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    now = datetime.now(timezone.utc)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE chunks, episode_vectors, episodes, podcasts, entity_mentions, entities CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
            (PODCAST_ID, "https://vec.test/feed.xml", "Vec Test Pod"),
        )
        for eid, title, pub in [
            (EPISODE_A, "Episode Alpha", now),
            (EPISODE_B, "Episode Beta", now),
        ]:
            conn.execute(
                """INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date)
                   VALUES (%s, %s, %s, %s, 'https://vec.test/a.mp3', %s)""",
                (eid, PODCAST_ID, f"ext-{title}", title, pub),
            )

    model = FakeEmbeddingModel()
    writer = PostgresChunkWriter(dsn=PG_DSN, embedding_model=model)
    writer.write_episode(
        EPISODE_A,
        _Transcript(
            [
                _Seg(0, "We talked at length about quantum computing", speaker="Alice"),
                _Seg(1, "Then a segment about sourdough baking", speaker="Bob"),
                _Seg(2, "um uh", kind="filler"),
            ]
        ),
    )
    writer.write_episode(
        EPISODE_B,
        _Transcript(
            [
                _Seg(0, "A full hour dedicated to quantum computing"),
                _Seg(1, "And nothing about anything else, just computing"),
            ]
        ),
    )
    return model, writer


def _backend(model):
    from thestill.search.pgvector_client import PgVectorBackend

    return PgVectorBackend(dsn=PG_DSN, embedding_model=model)


def test_writer_inserts_and_is_idempotent(seeded):
    import psycopg

    model, writer = seeded
    with psycopg.connect(PG_DSN) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        cents = conn.execute("SELECT COUNT(*) FROM episode_vectors").fetchone()[0]
    assert n == 4  # filler segment excluded
    assert cents == 2
    # Re-run without force → no new rows
    inserted = writer.write_episode(EPISODE_A, _Transcript([_Seg(0, "irrelevant computing")]))
    assert inserted == 0


def test_lexical_search_ranks_and_joins_metadata(seeded):
    from thestill.search.base import SearchMode

    model, _ = seeded
    hits = _backend(model).search("sourdough", mode=SearchMode.LEXICAL, limit=10, filters=None)
    assert len(hits) == 1
    h = hits[0]
    assert h.episode_id == EPISODE_A
    assert h.podcast_id == PODCAST_ID
    assert h.podcast_title == "Vec Test Pod"
    assert "sourdough" in h.text
    assert h.published_at is not None and h.published_at.tzinfo is not None


def test_lexical_negation_and_or(seeded):
    from thestill.search.base import SearchMode

    model, _ = seeded
    # negation: computing but NOT baking-segment text
    hits = _backend(model).search("computing -sourdough", mode=SearchMode.LEXICAL, limit=10, filters=None)
    assert hits, "negated query should still match computing chunks"
    assert all("sourdough" not in h.text for h in hits)
    # OR
    hits = _backend(model).search("sourdough OR quantum", mode=SearchMode.LEXICAL, limit=10, filters=None)
    assert len(hits) >= 3


def test_semantic_search_orders_by_cosine(seeded):
    from thestill.search.base import SearchMode

    model, _ = seeded
    hits = _backend(model).search("tell me about computing", mode=SearchMode.SEMANTIC, limit=4, filters=None)
    # 'computing'-topic chunks embed identically to the query (distance ~0);
    # the sourdough chunk is far and must be cut by the 0.85 threshold.
    assert 1 <= len(hits) <= 3
    assert all("computing" in h.text for h in hits)


def test_hybrid_fuses_both_legs(seeded):
    from thestill.search.base import SearchMode

    model, _ = seeded
    hits = _backend(model).search("quantum computing", mode=SearchMode.HYBRID, limit=5, filters=None)
    assert hits
    texts = [h.text for h in hits]
    assert any("quantum" in t for t in texts)


def test_filters_podcast_and_speaker(seeded):
    from thestill.search.base import SearchFilters, SearchMode

    model, _ = seeded
    be = _backend(model)
    # podcast filter matching
    hits = be.search("computing", mode=SearchMode.LEXICAL, limit=10, filters=SearchFilters(podcast_id=PODCAST_ID))
    assert hits
    # podcast filter excluding
    other = str(uuid.uuid4())
    hits = be.search("computing", mode=SearchMode.LEXICAL, limit=10, filters=SearchFilters(podcast_id=other))
    assert hits == []
    # speaker substring, case-insensitive (ILIKE)
    hits = be.search("sourdough", mode=SearchMode.LEXICAL, limit=10, filters=SearchFilters(speaker="bob"))
    assert len(hits) == 1
    hits = be.search("sourdough", mode=SearchMode.LEXICAL, limit=10, filters=SearchFilters(speaker="alice"))
    assert hits == []


def test_force_rewrite_replaces_rows(seeded):
    import psycopg

    model, writer = seeded
    inserted = writer.write_episode(EPISODE_A, _Transcript([_Seg(0, "totally new take on gardening")]), force=True)
    assert inserted == 1
    with psycopg.connect(PG_DSN) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunks WHERE episode_id = %s", (EPISODE_A,)).fetchone()[0]
    assert n == 1


def test_writer_sanitizes_control_chars(seeded):
    import psycopg

    model, writer = seeded
    # A NUL in segment text must be stripped (FM-7), not rejected by PG.
    inserted = writer.write_episode(EPISODE_B, _Transcript([_Seg(9, "saut\x00 onions cooking")]), force=True)
    assert inserted == 1
    with psycopg.connect(PG_DSN) as conn:
        txt = conn.execute("SELECT text FROM chunks WHERE episode_id = %s", (EPISODE_B,)).fetchone()[0]
    assert "\x00" not in txt and "saut onions" in txt
