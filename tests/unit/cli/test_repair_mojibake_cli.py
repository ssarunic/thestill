"""``thestill repair-mojibake`` — double-encoded UTF-8 in rows, files and the chunk index.

SQLite-backed; the Postgres branch differs only in placeholders and the
connection helper, which the corpus Postgres contract tests cover.
"""

from __future__ import annotations

import json
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

BAD = "Max JungestÃ¥l"
GOOD = "Max Jungestål"


def _vec(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    return struct.pack(f"<{_DIM}f", *v)


def _stub_encoders(monkeypatch) -> None:
    monkeypatch.setattr(EmbeddingModel, "encode_one", lambda self, text: _vec(hash(text) % 2**31))
    monkeypatch.setattr(
        EmbeddingModel, "encode_batch", lambda self, texts, batch_size=64: [_vec(hash(t) % 2**31) for t in texts]
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


def _seed(storage, db_path, monkeypatch) -> dict:
    """A podcast whose feed was decoded wrongly once: the damage sits in the episode
    row, the facts file, the cleaned transcript (md + sidecar), a summary, an
    entity with aliases, a mention, and the chunk rows embedded from the sidecar."""
    pm = PathManager(str(storage))
    pid, eid, ent_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    transcript = AnnotatedTranscript(
        episode_id=eid,
        segments=[
            AnnotatedSegment(
                id=0, start=0, end=1, text=f"{BAD} explains the raise in detail", speaker=BAD, kind="content"
            ),
            AnnotatedSegment(
                id=1, start=1, end=2, text="Jacob Effron asks about US expansion plans", speaker="Jacob", kind="content"
            ),
        ],
    )
    sidecar = pm.clean_transcript_file("p/ep_cleaned.json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    pm.clean_transcript_file("p/ep_cleaned.md").write_text(f"# Transcript\n\n**{BAD}:** hello\n", encoding="utf-8")
    summary = pm.summary_file("p/ep_summary.md")
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(f"# Summary\n\nGuest {BAD} talks.\n", encoding="utf-8")
    facts = pm.podcast_facts_file("p")
    facts.parent.mkdir(parents=True, exist_ok=True)
    facts.write_text(f"# Facts\n\nHosts: Jacob Effron, {BAD}\n", encoding="utf-8")
    ep_facts = pm.episode_facts_file("p", "ep")
    ep_facts.parent.mkdir(parents=True, exist_ok=True)
    ep_facts.write_text("# Episode facts\n\nGuests: nobody accented\n", encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, author) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, "https://x/f.xml", "Unsupervised Learning", "p", "Hosted by Jacob", f"Jacob, {BAD}"),
        )
        conn.execute(
            """INSERT INTO episodes (id, podcast_id, external_id, title, slug, audio_url, pub_date, description,
                                     clean_transcript_path, clean_transcript_json_path, summary_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid,
                pid,
                "ext",
                f"Behind Legora with {BAD}",
                "ep",
                "https://x/e.mp3",
                "2026-01-01T00:00:00",
                f"{BAD} joins to discuss the $550M raise",
                "p/ep_cleaned.md",
                "p/ep_cleaned.json",
                "p/ep_summary.md",
            ),
        )
        conn.execute(
            """INSERT INTO entities (id, type, canonical_name, aliases, wikidata_instance_of, created_at, updated_at)
               VALUES (?, 'person', ?, ?, '[]', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')""",
            (ent_id, BAD, json.dumps(["JungestÃ¥l", "Max"])),
        )
        conn.execute(
            """INSERT INTO entity_mentions (entity_id, resolution_status, episode_id, segment_id, start_ms, end_ms,
                                            speaker, surface_form, quote_excerpt, confidence, extractor, created_at)
               VALUES (?, 'resolved', ?, 0, 0, 1000, ?, ?, ?, 0.9, 'gemini', '2026-01-01T00:00:00+00:00')""",
            (ent_id, eid, BAD, "JungestÃ¥l", f"{BAD} explains the raise"),
        )
        conn.commit()

    _stub_encoders(monkeypatch)
    ChunkWriter(db_path=db_path, embedding_model=EmbeddingModel()).write_episode(eid, transcript)
    return {"pid": pid, "eid": eid, "ent_id": ent_id, "sidecar": sidecar, "facts": facts, "summary": summary}


def _chunk_texts(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        return [r[0] for r in conn.execute("SELECT text FROM chunks ORDER BY segment_id")]


def test_dry_run_reports_every_location_and_writes_nothing(cli_env, monkeypatch):
    storage, db_path = cli_env
    seeded = _seed(storage, db_path, monkeypatch)

    result = CliRunner().invoke(main, ["repair-mojibake"])

    assert result.exit_code == 0, result.output
    assert "dry run" in result.output
    for location in (
        "episodes.title",
        "episodes.description",
        "podcasts.author",
        "entities.canonical_name",
        "entities.aliases",
        "entity_mentions.speaker",
        "entity_mentions.quote_excerpt",
        "file",
    ):
        assert location in result.output, location
    assert "1 episode(s) to re-index" in result.output
    assert "--apply" in result.output
    # Nothing moved.
    assert BAD in seeded["sidecar"].read_text(encoding="utf-8")
    assert BAD in seeded["facts"].read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT title FROM episodes").fetchone()[0] == f"Behind Legora with {BAD}"
    assert any(BAD in t for t in _chunk_texts(db_path))


def test_apply_repairs_rows_files_and_reindexes_the_episode(cli_env, monkeypatch):
    storage, db_path = cli_env
    seeded = _seed(storage, db_path, monkeypatch)

    result = CliRunner().invoke(main, ["repair-mojibake", "--apply"])

    assert result.exit_code == 0, result.output
    assert "applied" in result.output
    assert "1 episode(s) re-indexed" in result.output

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT title, description FROM episodes").fetchone() == (
            f"Behind Legora with {GOOD}",
            f"{GOOD} joins to discuss the $550M raise",
        )
        assert conn.execute("SELECT author FROM podcasts").fetchone()[0] == f"Jacob, {GOOD}"
        name, aliases = conn.execute("SELECT canonical_name, aliases FROM entities").fetchone()
        assert name == GOOD
        assert json.loads(aliases) == ["Jungestål", "Max"]
        assert conn.execute("SELECT speaker, surface_form, quote_excerpt FROM entity_mentions").fetchone() == (
            GOOD,
            "Jungestål",
            f"{GOOD} explains the raise",
        )
    for path in ("sidecar", "facts", "summary"):
        text = seeded[path].read_text(encoding="utf-8")
        assert GOOD in text and BAD not in text, path
    AnnotatedTranscript.model_validate_json(seeded["sidecar"].read_text(encoding="utf-8"))  # still valid JSON
    texts = _chunk_texts(db_path)
    assert len(texts) == 2
    assert any(GOOD in t for t in texts) and not any(BAD in t for t in texts)

    # Idempotent: a second run finds nothing.
    again = CliRunner().invoke(main, ["repair-mojibake"])
    assert again.exit_code == 0, again.output
    assert "no double-encoded text found" in again.output


def test_clean_corpus_reports_nothing(cli_env, monkeypatch):
    storage, db_path = cli_env
    _stub_encoders(monkeypatch)
    result = CliRunner().invoke(main, ["repair-mojibake", "--apply"])
    assert result.exit_code == 0, result.output
    assert "no double-encoded text found" in result.output


def test_unknown_podcast_id_fails_cleanly(cli_env, monkeypatch):
    _stub_encoders(monkeypatch)
    result = CliRunner().invoke(main, ["repair-mojibake", "--podcast-id", "nope"])
    assert result.exit_code == 1
    assert "No podcast with id=nope" in result.output
