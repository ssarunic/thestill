"""``thestill chunks verify`` — stored vectors vs the configured model.

SQLite-backed; the Postgres branch of ``_sample_rows`` is one query away
and covered by the integration suite's schema tests.
"""

from __future__ import annotations

import sqlite3
import struct
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy")

from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.search.index_verify import DISAGREEMENT_THRESHOLD, verify_index
from thestill.utils.sqlite_ext import maybe_load_vec_extension

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return struct.pack(f"<{_DIM}f", *v)


class _Stub(EmbeddingModel):
    """Deterministic: vector = f(text, offset). ``batch_offset`` lets a test make
    the writer path disagree with the query path on purpose."""

    def __init__(self, offset: int = 0, batch_offset: int | None = None):
        self.model_name = DEFAULT_EMBEDDING_MODEL
        self.dim = _DIM
        self.offset = offset
        self.batch_offset = offset if batch_offset is None else batch_offset

    def encode_one(self, text: str) -> bytes:  # type: ignore[override]
        return _vec((hash(text) + self.offset) % 2**31)

    def encode_batch(self, texts, *, batch_size: int = 64):  # type: ignore[override]
        return [_vec((hash(t) + self.batch_offset) % 2**31) for t in texts]


def _seed(tmp_path, model: EmbeddingModel) -> SimpleNamespace:
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    pid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        maybe_load_vec_extension(conn)
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)", (pid, "https://x/f.xml", "P", "p")
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date) VALUES (?, ?, ?, ?, ?, ?)",
            (eid, pid, "ext", "E", "https://x/e.mp3", "2026-01-01T00:00:00"),
        )
        conn.commit()
    texts = [f"segment number {i} talking about something at length" for i in range(6)]
    ChunkWriter(db_path=db_path, embedding_model=model).write_episode(
        eid,
        AnnotatedTranscript(
            episode_id=eid,
            segments=[
                AnnotatedSegment(id=i, start=i, end=i + 1, text=t, speaker="Host", kind="content")
                for i, t in enumerate(texts)
            ],
        ),
    )
    return SimpleNamespace(database_url="", database_path=db_path)


def test_consistent_index_reads_zero(tmp_path):
    config = _seed(tmp_path, _Stub())
    report = verify_index(config, _Stub(), sample=50)
    assert report.sampled == 6
    assert report.stored_vs_query_max < 1e-5
    assert report.batch_vs_query_max < 1e-5
    assert report.healthy
    assert report.verdict == "consistent"


def test_rows_from_another_model_are_flagged_for_backfill(tmp_path):
    config = _seed(tmp_path, _Stub(offset=0))
    report = verify_index(config, _Stub(offset=7), sample=50)  # same label, different vectors
    assert report.index_disagrees
    assert not report.runtime_disagrees
    assert report.stored_vs_query_max > DISAGREEMENT_THRESHOLD
    assert "backfill --force" in report.verdict
    assert len(report.worst) == 3 and all(d > DISAGREEMENT_THRESHOLD for _, d in report.worst)


def test_writer_and_query_paths_disagreeing_is_a_runtime_verdict(tmp_path):
    config = _seed(tmp_path, _Stub(offset=0))
    report = verify_index(config, _Stub(offset=0, batch_offset=3), sample=50)
    assert report.runtime_disagrees
    assert "backfill would not help" in report.verdict
    assert not report.healthy


def test_empty_index_has_nothing_to_verify(tmp_path):
    db_path = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    report = verify_index(SimpleNamespace(database_url="", database_path=db_path), _Stub(), sample=10)
    assert report.sampled == 0
    assert not report.healthy
    assert "nothing to verify" in report.verdict


def test_sample_caps_rows(tmp_path):
    config = _seed(tmp_path, _Stub())
    assert verify_index(config, _Stub(), sample=2).sampled == 2
