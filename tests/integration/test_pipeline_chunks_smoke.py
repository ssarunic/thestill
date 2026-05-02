"""Spec #28 §2.10 — pipeline → chunks → search smoke integration test.

End-to-end pass for the new search path: write chunks via
``ChunkWriter``, then query them via ``SqliteVecBackend``. Uses a
deterministic stub embedding model so semantic matches are
predictable.
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
from thestill.models.entities import MatchType
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, SearchMode, embedding_dim_for
from thestill.search.sqlite_vec_client import SqliteVecBackend
from thestill.utils.sqlite_ext import maybe_load_vec_extension

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v) or 1.0
    return struct.pack(f"<{_DIM}f", *v)


class _DeterministicEmbedding(EmbeddingModel):
    """Hash-keyed stub so the same text always yields the same vector."""

    def __init__(self):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM

    def encode_one(self, text: str) -> bytes:
        return _vec(hash(text) % 2**31)

    def encode_batch(self, texts, *, batch_size: int = 64):
        return [self.encode_one(t) for t in texts]


def _seed(tmp_path) -> tuple[str, str, str]:
    """Set up a podcast + episode in a fresh DB. Returns (db_path, podcast_id, episode_id)."""
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcast_id = str(uuid.uuid4())
    episode_id = "11111111-2222-3333-4444-555555555555"
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Smoke Show", "smoke-show"),
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                episode_id,
                podcast_id,
                "ext-1",
                "Smoke Episode",
                "https://example.com/ep.mp3",
                datetime(2026, 4, 28).isoformat(),
            ),
        )
        conn.commit()
    return db_path, podcast_id, episode_id


def _transcript(*specs) -> AnnotatedTranscript:
    return AnnotatedTranscript(
        episode_id="ep",
        segments=[
            AnnotatedSegment(id=sid, start=s, end=e, text=t, speaker=spk, kind="content") for sid, s, e, t, spk in specs
        ],
    )


def test_chunks_write_then_search_end_to_end(tmp_path):
    db_path, _podcast_id, episode_id = _seed(tmp_path)
    embedding = _DeterministicEmbedding()
    writer = ChunkWriter(db_path=db_path, embedding_model=embedding)
    transcript = _transcript(
        (0, 1.0, 5.0, "agentic engineering at scale", "Host"),
        (1, 5.0, 10.0, "cooking pasta sauce", "Host"),
        (2, 10.0, 15.0, "more on agentic systems", "Host"),
    )
    inserted = writer.write_episode(episode_id, transcript)
    assert inserted == 3

    backend = SqliteVecBackend(db_path=db_path, embedding_model=embedding)

    lex_hits = backend.search("agentic", mode=SearchMode.LEXICAL, limit=5, filters=None)
    assert len(lex_hits) == 2
    assert {h.segment_id for h in lex_hits} == {0, 2}
    assert all(h.match_type == MatchType.LEXICAL for h in lex_hits)
    assert all(h.podcast_title == "Smoke Show" for h in lex_hits)

    sem_hits = backend.search(
        "Host: agentic engineering at scale",
        mode=SearchMode.SEMANTIC,
        limit=5,
        filters=None,
    )
    assert len(sem_hits) >= 1
    assert sem_hits[0].segment_id == 0
    assert sem_hits[0].match_type == MatchType.SEMANTIC

    hybrid_hits = backend.search("agentic", mode=SearchMode.HYBRID, limit=5, filters=None)
    assert len(hybrid_hits) >= 1
    assert all(h.match_type == MatchType.HYBRID for h in hybrid_hits)
    assert {h.segment_id for h in hybrid_hits} >= {0, 2}
