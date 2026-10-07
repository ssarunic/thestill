"""CliRunner smoke tests for ``thestill relink-direct`` (spec #81 Phase 4)."""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.entities import EntityMention, EntityRecord, EntityType, ResolutionStatus
from thestill.repositories.sqlite_entity_repository import SqliteEntityRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def db(tmp_path, monkeypatch):
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db_path)
    podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, 'https://x/f.xml', 'P', 'p')", (podcast_id,)
        )
        conn.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, slug, description, audio_url) "
            "VALUES (?, ?, 'e1', 'E', 'e', '', 'https://x/a.mp3')",
            (episode_id, podcast_id),
        )
    repo = SqliteEntityRepository(db_path=db_path)
    repo.upsert_entity(EntityRecord(id="topic:love", type=EntityType.TOPIC, canonical_name="Love", wikidata_qid="Q316"))
    repo.insert_mentions(
        [
            EntityMention(
                entity_id="topic:love",
                resolution_status=ResolutionStatus.RESOLVED,
                resolution_method="direct",
                episode_id=episode_id,
                segment_id=i,
                start_ms=0,
                end_ms=1000,
                surface_form=surface,
                surface_label="topic",
                quote_excerpt=surface,
                confidence=0.9,
                extractor="gliner:test",
            )
            for i, surface in enumerate(["healthcare", "love", "Every single employee"])
        ]
    )
    return db_path, episode_id


def _run(*args, code=0):
    result = CliRunner().invoke(main, ["relink-direct", *args], catch_exceptions=False)
    assert result.exit_code == code, result.output
    return result.output


def _statuses(db_path):
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT surface_form, resolution_status FROM entity_mentions"))


def test_dry_run_reports_and_writes_nothing(db):
    db_path, _ = db
    out = _run("--dry-run")
    assert "ReFinED links in scope: 3  selected: 2 in 1 episode(s)" in out and "Love" in out
    assert set(_statuses(db_path).values()) == {"resolved"}


def test_run_resets_only_unrelated_names_and_enqueues_the_episode(db):
    db_path, episode_id = db
    out = _run()
    assert "Reset 2 mention(s); enqueued resolve-entities for 1 episode(s)" in out
    assert _statuses(db_path) == {"healthcare": "pending", "love": "resolved", "Every single employee": "pending"}
    with sqlite3.connect(db_path) as conn:
        tasks = conn.execute("SELECT episode_id, stage FROM tasks").fetchall()
    assert tasks == [(episode_id, "resolve-entities")]


def test_a_busy_queue_is_refused(db):
    db_path, _ = db
    _run("--all", "--max-open-tasks", "0")  # enqueues one task
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE entity_mentions SET resolution_status='resolved', resolution_method='direct', entity_id='topic:love'"
        )
    out = _run("--max-open-tasks", "0", code=1)
    assert "already open" in out
