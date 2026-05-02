"""Spec #28 §2.10.3 — ChunkWriter unit tests.

The real embedding model is ~470 MB; we use a stub that returns
deterministic zero-vectors so the migration's ``chunks_vec`` insert
trigger receives bytes of the right shape.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from datetime import datetime

import pytest

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.utils.sqlite_ext import maybe_load_vec_extension

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required for chunks tests")


class _StubEmbeddingModel(EmbeddingModel):
    """Bypasses sentence-transformers; returns zero-vectors of the right dim."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL):
        self.model_name = model_name
        self.dim = embedding_dim_for(model_name)
        self._calls = 0

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        self._calls += 1
        return struct.pack(f"<{self.dim}f", *([0.0] * self.dim))

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        self._calls += len(texts)
        return [struct.pack(f"<{self.dim}f", *([0.0] * self.dim)) for _ in texts]


def _seed_db(tmp_path) -> tuple[str, str]:
    """Create a podcasts.db with one podcast + one episode. Returns (db_path, episode_id)."""
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)  # runs migrations
    podcast_id = str(uuid.uuid4())
    episode_id = "11111111-2222-3333-4444-555555555555"
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
            (podcast_id, "https://example.com/feed.xml", "Fixture", "fixture"),
        )
        conn.execute(
            """
            INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                podcast_id,
                "ext-1",
                "Sample Episode",
                "https://example.com/ep1.mp3",
                datetime(2026, 4, 28).isoformat(),
            ),
        )
        conn.commit()
    return db_path, episode_id


def _transcript(*specs: tuple[int, float, float, str, str | None]) -> AnnotatedTranscript:
    return AnnotatedTranscript(
        episode_id="ep-1",
        segments=[
            AnnotatedSegment(id=sid, start=s, end=e, text=t, speaker=spk, kind="content") for sid, s, e, t, spk in specs
        ],
    )


class TestChunkWriter:
    def test_writes_one_row_per_content_segment(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = _transcript(
            (0, 1.0, 5.0, "Hello world.", "Host"),
            (1, 5.0, 10.0, "Second segment.", "Guest"),
        )
        inserted = writer.write_episode(episode_id, transcript)
        assert inserted == 2

        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            rows = conn.execute(
                "SELECT segment_id, start_ms, end_ms, speaker, text FROM chunks WHERE episode_id = ? ORDER BY segment_id",
                (episode_id,),
            ).fetchall()
        assert rows == [
            (0, 1000, 5000, "Host", "Host: Hello world."),
            (1, 5000, 10000, "Guest", "Guest: Second segment."),
        ]

    def test_idempotent_skip_by_default(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = _transcript((0, 1.0, 5.0, "Same.", "Host"))
        first = writer.write_episode(episode_id, transcript)
        assert first == 1
        second = writer.write_episode(episode_id, transcript)
        assert second == 0  # skipped

        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            count = conn.execute("SELECT COUNT(*) FROM chunks WHERE episode_id = ?", (episode_id,)).fetchone()[0]
        assert count == 1

    def test_force_re_embeds(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = _transcript((0, 1.0, 5.0, "Initial.", "Host"))
        writer.write_episode(episode_id, transcript)

        new_transcript = _transcript((0, 1.0, 5.0, "Replaced.", "Host"))
        inserted = writer.write_episode(episode_id, new_transcript, force=True)
        assert inserted == 1

        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            text = conn.execute("SELECT text FROM chunks WHERE episode_id = ?", (episode_id,)).fetchone()[0]
        assert text == "Host: Replaced."

    def test_skips_non_content_segments(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = AnnotatedTranscript(
            episode_id="ep-1",
            segments=[
                AnnotatedSegment(id=0, start=0.0, end=5.0, text="Ad copy.", kind="ad_break"),
                AnnotatedSegment(id=1, start=5.0, end=10.0, text="Real talk.", kind="content"),
            ],
        )
        inserted = writer.write_episode(episode_id, transcript)
        assert inserted == 1

    def test_empty_transcript_returns_zero(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        empty = AnnotatedTranscript(episode_id="ep-1", segments=[])
        assert writer.write_episode(episode_id, empty) == 0

    def test_segment_text_includes_speaker_prefix(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = _transcript(
            (0, 1.0, 5.0, "No speaker here.", None),
            (1, 5.0, 10.0, "Has speaker.", "Karpathy"),
        )
        writer.write_episode(episode_id, transcript)
        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            rows = conn.execute(
                "SELECT text FROM chunks WHERE episode_id = ? ORDER BY segment_id", (episode_id,)
            ).fetchall()
        assert rows[0][0] == "No speaker here."
        assert rows[1][0] == "Karpathy: Has speaker."

    def test_triggers_populate_chunks_vec_and_chunks_fts(self, tmp_path):
        db_path, episode_id = _seed_db(tmp_path)
        writer = ChunkWriter(db_path=db_path, embedding_model=_StubEmbeddingModel())
        transcript = _transcript((0, 1.0, 5.0, "Trigger payload.", "Host"))
        writer.write_episode(episode_id, transcript)

        with sqlite3.connect(db_path) as conn:
            maybe_load_vec_extension(conn)
            vec_count = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
            fts_count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        assert vec_count == 1
        assert fts_count == 1
