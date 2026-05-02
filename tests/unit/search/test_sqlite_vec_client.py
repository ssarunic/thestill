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

import numpy as np
import pytest

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.models.entities import EntityRecord, EntityType, MatchType
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, SearchFilters, SearchMode, embedding_dim_for
from thestill.search.sqlite_vec_client import SqliteVecBackend
from thestill.utils.sqlite_ext import maybe_load_vec_extension

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")


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
                (1, 5.0, 10.0, "Cooking is fun.", "Host"),
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
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "Just some text.", "Host")])
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        assert backend.search("nonexistent", mode=SearchMode.LEXICAL, limit=10, filters=None) == []

    def test_metadata_joined_in_hit(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "agentic stuff", "Host")])
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
                (0, 1.0, 5.0, "alpha text", "Host"),
                (1, 5.0, 10.0, "beta text", "Host"),
            ],
        )
        # Backend reuses the stub for query encoding → same hash, same
        # vector as the "alpha text" segment, so it wins the k-NN.
        backend = SqliteVecBackend(db_path=db_path, embedding_model=_StubEmbeddingModel())
        hits = backend.search("Host: alpha text", mode=SearchMode.SEMANTIC, limit=2, filters=None)
        assert len(hits) == 2
        assert hits[0].segment_id == 0
        assert hits[0].match_type == MatchType.SEMANTIC
        assert hits[0].score < hits[1].score


class TestHybridMode:
    def test_fuses_lexical_and_semantic_ranks(self, tmp_path):
        db_path, fixtures = _seed_db(tmp_path)
        e1 = fixtures["episodes"]["e1"]["id"]
        _populate_chunks(
            db_path,
            e1,
            [
                (0, 1.0, 5.0, "agentic engineering rocks", "Host"),
                (1, 5.0, 10.0, "unrelated topic chatter", "Host"),
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
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "shared term here", "Host")])
        _populate_chunks(db_path, e3, [(0, 1.0, 5.0, "shared term here", "Host")])

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
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "matchword", "Host")])
        _populate_chunks(db_path, e2, [(0, 1.0, 5.0, "matchword", "Host")])
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
        _populate_chunks(db_path, e1, [(0, 1.0, 5.0, "matchword here", "Host")])
        _populate_chunks(db_path, e2, [(0, 1.0, 5.0, "matchword here", "Host")])
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
