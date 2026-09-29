"""``thestill repair-mojibake`` against Postgres: rows, files and pgvector chunks.

The SQLite CliRunner test covers the report and the file pass; this one
proves the Postgres branch (``%s`` placeholders, keyset pagination over
``id``, the pgvector writer) end to end. Skipped without a reachable
``TEST_DATABASE_URL``; the test truncates the tables it seeds, so point it
at a scratch database only.
"""

from __future__ import annotations

import json
import os
import struct
import uuid

import pytest
from click.testing import CliRunner

np = pytest.importorskip("numpy")
psycopg = pytest.importorskip("psycopg")

from thestill.cli import main
from thestill.core.embedding_model import EmbeddingModel
from thestill.core.postgres_chunk_writer import PostgresChunkWriter
from thestill.models.annotated_transcript import AnnotatedSegment, AnnotatedTranscript
from thestill.repositories.postgres_schema import ensure_schema
from thestill.search.base import DEFAULT_EMBEDDING_MODEL, embedding_dim_for
from thestill.utils.path_manager import PathManager

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
_DIM = embedding_dim_for(DEFAULT_EMBEDDING_MODEL)
BAD = "Max JungestÃ¥l"
GOOD = "Max Jungestål"


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(PG_DSN),
    reason="Postgres not reachable — set TEST_DATABASE_URL to run the repair-mojibake contract test",
)


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return struct.pack(f"<{_DIM}f", *v)


@pytest.fixture
def pg_cli_env(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.setenv("DATABASE_URL", PG_DSN)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(EmbeddingModel, "encode_one", lambda self, text: _vec(hash(text) % 2**31))
    monkeypatch.setattr(
        EmbeddingModel, "encode_batch", lambda self, texts, batch_size=64: [_vec(hash(t) % 2**31) for t in texts]
    )
    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE chunks, episode_vectors, entity_mentions, entities, episodes, podcasts CASCADE")
    yield storage
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE chunks, episode_vectors, entity_mentions, entities, episodes, podcasts CASCADE")


def test_apply_repairs_postgres_rows_and_reindexes_pgvector_chunks(pg_cli_env):
    storage = pg_cli_env
    pm = PathManager(str(storage))
    pid, eid, ent_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    transcript = AnnotatedTranscript(
        episode_id=eid,
        segments=[
            AnnotatedSegment(
                id=0, start=0, end=1, text=f"{BAD} explains the raise — “we’re doubling”", speaker=BAD, kind="content"
            ),
            AnnotatedSegment(
                id=1, start=1, end=2, text="Jacob Effron asks about US expansion plans", speaker="Jacob", kind="content"
            ),
        ],
    )
    sidecar = pm.clean_transcript_file("p/ep_cleaned.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    with psycopg.connect(PG_DSN) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, author) VALUES (%s, %s, %s, %s, %s, %s)",
            (pid, "https://x/f.xml", "Unsupervised Learning", "p", "Hosted by Jacob", f"Jacob, {BAD}"),
        )
        conn.execute(
            """INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date, description,
                                     clean_transcript_json_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                eid,
                pid,
                "ext",
                f"Behind Legora with {BAD}",
                "ep",
                "https://x/e.mp3",
                "2026-01-01T00:00:00+00:00",
                f"{BAD} joins to discuss the raise",
                "p/ep_cleaned.json",
            ),
        )
        conn.execute(
            """INSERT INTO entities (id, type, canonical_name, aliases, wikidata_instance_of, created_at, updated_at)
               VALUES (%s, 'person', %s, %s, '[]', now(), now())""",
            (ent_id, BAD, json.dumps(["JungestÃ¥l", "Max"])),
        )
        conn.execute(
            """INSERT INTO entity_mentions (entity_id, resolution_status, episode_id, segment_id, start_ms, end_ms,
                                            speaker, surface_form, quote_excerpt, confidence, extractor, created_at)
               VALUES (%s, 'resolved', %s, 0, 0, 1000, %s, %s, %s, 0.9, 'gemini', now())""",
            (ent_id, eid, BAD, "JungestÃ¥l", f"{BAD} explains the raise"),
        )
    PostgresChunkWriter(dsn=PG_DSN, embedding_model=EmbeddingModel()).write_episode(eid, transcript)

    dry = CliRunner().invoke(main, ["repair-mojibake"])
    assert dry.exit_code == 0, dry.output
    assert "1 episode(s) to re-index" in dry.output
    assert "entities.aliases" in dry.output

    applied = CliRunner().invoke(main, ["repair-mojibake", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "1 episode(s) re-indexed, 2 rows written" in applied.output

    with psycopg.connect(PG_DSN) as conn:
        assert conn.execute("SELECT title, description FROM episodes").fetchone() == (
            f"Behind Legora with {GOOD}",
            f"{GOOD} joins to discuss the raise",
        )
        assert conn.execute("SELECT author FROM podcasts").fetchone()[0] == f"Jacob, {GOOD}"
        name, aliases = conn.execute("SELECT canonical_name, aliases FROM entities").fetchone()
        assert name == GOOD
        assert (aliases if isinstance(aliases, list) else json.loads(aliases)) == ["Jungestål", "Max"]
        assert conn.execute("SELECT speaker, surface_form, quote_excerpt FROM entity_mentions").fetchone() == (
            GOOD,
            "Jungestål",
            f"{GOOD} explains the raise",
        )
        texts = [r[0] for r in conn.execute("SELECT text FROM chunks ORDER BY segment_id")]
    assert len(texts) == 2
    assert any(GOOD in t for t in texts) and not any(BAD in t for t in texts)
    assert GOOD in sidecar.read_text(encoding="utf-8")

    again = CliRunner().invoke(main, ["repair-mojibake"])
    assert again.exit_code == 0, again.output
    assert "no double-encoded text found" in again.output
