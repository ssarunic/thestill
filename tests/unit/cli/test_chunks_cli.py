"""CliRunner tests for ``thestill chunks backfill`` and ``thestill chunks verify``."""

from __future__ import annotations

import sqlite3
import struct
import uuid

import pytest
from click.testing import CliRunner

pytest.importorskip("sqlite_vec", reason="sqlite-vec extension required")
np = pytest.importorskip("numpy")

from thestill.cli import main
from thestill.core.chunk_writer import ChunkWriter
from thestill.core.embedding_model import EmbeddingModel
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.utils.path_manager import PathManager

_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return struct.pack(f"<{_DIM}f", *v)


def _stub_encoders(monkeypatch, offset: int = 0) -> None:
    """Make every EmbeddingModel deterministic and free of sentence-transformers."""
    monkeypatch.setattr(EmbeddingModel, "encode_one", lambda self, text: _vec((hash(text) + offset) % 2**31))
    monkeypatch.setattr(
        EmbeddingModel,
        "encode_batch",
        lambda self, texts, batch_size=64: [_vec((hash(t) + offset) % 2**31) for t in texts],
    )


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    return storage, str(db_path)


def _seed_episode_with_sidecar(storage, db_path) -> tuple[str, AnnotatedTranscript]:
    pid, eid = str(uuid.uuid4()), str(uuid.uuid4())
    transcript = AnnotatedTranscript(
        episode_id=eid,
        segments=[
            AnnotatedSegment(
                id=i,
                start=i,
                end=i + 1,
                text=f"segment {i} with plenty of words to index",
                speaker="Host",
                kind="content",
            )
            for i in range(3)
        ],
    )
    sidecar = PathManager(str(storage)).clean_transcript_file("ep_cleaned.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(transcript.model_dump_json(), encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)", (pid, "https://x/f.xml", "P", "p")
        )
        conn.execute(
            """INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, pub_date, clean_transcript_json_path)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (eid, pid, "ext", "E", "https://x/e.mp3", "2026-01-01T00:00:00", "ep_cleaned.json"),
        )
        conn.commit()
    return eid, transcript


class _RecordingWriter:
    def __init__(self):
        self.calls = []

    def write_episode(self, episode_id, transcript, *, force=False):
        self.calls.append((episode_id, len(transcript.segments), force))
        return 3


def test_backfill_uses_the_backend_resolved_writer(cli_env, monkeypatch):
    """The command must go through the factory, not build the SQLite writer itself —
    on a Postgres deployment the latter wrote into a stray podcasts.db."""
    storage, db_path = cli_env
    eid, _ = _seed_episode_with_sidecar(storage, db_path)
    _stub_encoders(monkeypatch)
    writer = _RecordingWriter()
    seen = {}

    def fake_factory(config, embedding_model):
        seen["config"] = config
        seen["model"] = embedding_model
        return writer

    from thestill.repositories import factory

    monkeypatch.setattr(factory, "make_chunk_writer", fake_factory)
    result = CliRunner().invoke(main, ["chunks", "backfill", "--force", "--max-episodes", "5"])
    assert result.exit_code == 0, result.output
    assert writer.calls == [(eid, 3, True)]
    assert seen["config"].database_path is not None
    assert isinstance(seen["model"], EmbeddingModel)
    assert "3 inserted" in result.output


def test_verify_reports_consistent_and_exits_zero(cli_env, monkeypatch):
    storage, db_path = cli_env
    eid, transcript = _seed_episode_with_sidecar(storage, db_path)
    _stub_encoders(monkeypatch)
    ChunkWriter(db_path=db_path, embedding_model=EmbeddingModel()).write_episode(eid, transcript)

    result = CliRunner().invoke(main, ["chunks", "verify", "--sample", "10"])
    assert result.exit_code == 0, result.output
    assert "Sampled: 3" in result.output
    assert "Verdict: consistent" in result.output


def test_verify_flags_a_stale_index_with_exit_code_two(cli_env, monkeypatch):
    storage, db_path = cli_env
    eid, transcript = _seed_episode_with_sidecar(storage, db_path)
    _stub_encoders(monkeypatch, offset=0)
    ChunkWriter(db_path=db_path, embedding_model=EmbeddingModel()).write_episode(eid, transcript)
    _stub_encoders(monkeypatch, offset=11)  # the "model" answering queries now differs from the writer

    result = CliRunner().invoke(main, ["chunks", "verify"])
    assert result.exit_code == 2, result.output
    assert "backfill --force" in result.output
    assert "worst" in result.output
