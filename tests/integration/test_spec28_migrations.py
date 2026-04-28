"""Spec #28 — migration idempotency + tasks-CHECK rebuild.

The migration code lives in two places:

- ``SqlitePodcastRepository._run_migrations`` — adds the
  ``entity_extraction_status`` column on ``episodes`` and creates
  ``entities`` / ``entity_mentions`` / ``entity_cooccurrences`` tables.
- ``QueueManager._ensure_table`` — extends the ``tasks.stage`` CHECK
  constraint to include the four new entity-branch stages, via a
  full SQLite table rebuild.

These tests confirm:

1. Running the migration twice on the same DB is a no-op.
2. A pre-spec-28 ``tasks`` table (with the old 5-stage CHECK) is
   rebuilt and accepts inserts of the new stage names.
3. New databases get the new CHECK directly via ``CREATE TABLE``.
"""

import sqlite3
import uuid
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


@pytest.fixture
def tmp_db(tmp_path) -> Path:
    return tmp_path / "thestill.db"


def _setup_legacy_db(db_path: Path) -> None:
    """Stand up a pre-spec-28-shaped database.

    Mirrors the production init order: ``SqlitePodcastRepository`` runs
    first (creating ``podcasts``/``episodes`` plus all the spec #28
    entity tables), then ``QueueManager`` would run. We slip in a
    legacy ``tasks`` table between those two steps so the rebuild
    migration has something to migrate from.

    The FK to ``episodes`` requires the parent table to exist — this
    matches production where ``episodes`` is always present before any
    ``tasks`` row is inserted.
    """
    SqlitePodcastRepository(db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    try:
        # Create the legacy table directly. ``CREATE TABLE IF NOT EXISTS``
        # would be safer in general but we want to fail loudly if a
        # prior init left a ``tasks`` table behind.
        conn.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY NOT NULL,
                episode_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                error_message TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                FOREIGN KEY (episode_id) REFERENCES episodes(id),
                CHECK (length(id) = 36),
                CHECK (stage IN ('download', 'downsample', 'transcribe', 'clean', 'summarize'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _stage_check_clause(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
        row = cursor.fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


class TestPodcastRepoMigrationIsIdempotent:
    def test_double_init_does_not_error(self, tmp_db):
        SqlitePodcastRepository(db_path=str(tmp_db))
        SqlitePodcastRepository(db_path=str(tmp_db))  # second init = pure migrations pass

    def test_entity_tables_present_after_init(self, tmp_db):
        SqlitePodcastRepository(db_path=str(tmp_db))
        conn = sqlite3.connect(str(tmp_db))
        try:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"entities", "entity_mentions", "entity_cooccurrences"} <= tables

            cursor = conn.execute("PRAGMA table_info(episodes)")
            episode_columns = {r[1] for r in cursor.fetchall()}
            assert "entity_extraction_status" in episode_columns
        finally:
            conn.close()

    def test_entity_indexes_present(self, tmp_db):
        SqlitePodcastRepository(db_path=str(tmp_db))
        conn = sqlite3.connect(str(tmp_db))
        try:
            indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            for required in (
                "idx_entities_type",
                "idx_entities_wikidata",
                "idx_mentions_entity",
                "idx_mentions_episode",
                "idx_mentions_role",
                "idx_mentions_pending",
            ):
                assert required in indexes, f"missing index {required}"
        finally:
            conn.close()


class TestTasksTableRebuild:
    def test_legacy_tasks_table_is_rebuilt_to_accept_new_stages(self, tmp_db):
        _setup_legacy_db(tmp_db)

        # Sanity: the pre-migration table actually rejects the new stage.
        conn = sqlite3.connect(str(tmp_db))
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tasks (id, episode_id, stage) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), "ep-1", "extract-entities"),
                )
            conn.rollback()
        finally:
            conn.close()

        # Construction runs the rebuild migration.
        QueueManager(db_path=str(tmp_db))

        # Post-migration: the new stage is accepted, the old stages still work.
        conn = sqlite3.connect(str(tmp_db))
        try:
            for stage_value in (
                "download",
                "summarize",
                "extract-entities",
                "resolve-entities",
                "write-corpus",
                "reindex",
            ):
                conn.execute(
                    "INSERT INTO tasks (id, episode_id, stage) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), "ep-1", stage_value),
                )
            conn.commit()

            # And the bogus value is still rejected — proves we kept a CHECK.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO tasks (id, episode_id, stage) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), "ep-1", "totally-not-a-stage"),
                )
            conn.rollback()
        finally:
            conn.close()

    def test_rebuild_runs_at_most_once(self, tmp_db):
        """Second QueueManager init must NOT touch the (now-correct) table."""
        _setup_legacy_db(tmp_db)
        QueueManager(db_path=str(tmp_db))
        sql_after_first = _stage_check_clause(tmp_db)
        QueueManager(db_path=str(tmp_db))
        sql_after_second = _stage_check_clause(tmp_db)
        assert sql_after_first == sql_after_second
        assert "extract-entities" in sql_after_second

    def test_fresh_db_uses_extended_check_directly(self, tmp_db):
        QueueManager(db_path=str(tmp_db))
        sql = _stage_check_clause(tmp_db)
        assert "extract-entities" in sql
        assert "reindex" in sql

    def test_existing_rows_preserved_through_rebuild(self, tmp_db):
        _setup_legacy_db(tmp_db)

        # Seed a podcast + episode the task rows can reference. The
        # rebuild runs with ``PRAGMA foreign_keys = ON`` (set in
        # ``QueueManager._get_connection``), so orphan ``tasks.episode_id``
        # values would fail the ``INSERT INTO tasks_new SELECT FROM tasks``.
        # In production episodes always exist before tasks are created;
        # the test mirrors that.
        podcast_id = str(uuid.uuid4())
        episode_id = str(uuid.uuid4())
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/feed.xml", "Fixture Podcast", "fixture-podcast"),
            )
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
                (episode_id, podcast_id, "ep-external-1", "Fixture Episode", "https://example.com/ep1.mp3"),
            )
            conn.commit()
        finally:
            conn.close()

        legacy_ids = [str(uuid.uuid4()) for _ in range(3)]
        conn = sqlite3.connect(str(tmp_db))
        try:
            for tid, stage in zip(legacy_ids, ["download", "transcribe", "summarize"]):
                conn.execute(
                    "INSERT INTO tasks (id, episode_id, stage, status) VALUES (?, ?, ?, ?)",
                    (tid, episode_id, stage, "completed"),
                )
            conn.commit()
        finally:
            conn.close()

        QueueManager(db_path=str(tmp_db))

        conn = sqlite3.connect(str(tmp_db))
        try:
            cursor = conn.execute("SELECT id, stage FROM tasks ORDER BY stage")
            rows = cursor.fetchall()
            ids_after = {row[0] for row in rows}
            assert set(legacy_ids) == ids_after
        finally:
            conn.close()


class TestStageEnumIntegrity:
    def test_every_taskstage_value_passes_check(self, tmp_db):
        # Seed a real episode so we don't accidentally rely on raw
        # ``sqlite3.connect`` having ``foreign_keys`` off — that would
        # quietly mask FK violations. With ``PRAGMA foreign_keys = ON``
        # below, the inserts below are exercising both the CHECK and
        # the FK against ``episodes(id)``.
        SqlitePodcastRepository(db_path=str(tmp_db))
        QueueManager(db_path=str(tmp_db))

        podcast_id = str(uuid.uuid4())
        episode_id = str(uuid.uuid4())
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, "https://example.com/feed.xml", "Fixture Podcast", "fixture-podcast"),
            )
            conn.execute(
                "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, ?, ?)",
                (episode_id, podcast_id, "ep-external-1", "Fixture Episode", "https://example.com/ep1.mp3"),
            )
            for stage in TaskStage:
                conn.execute(
                    "INSERT INTO tasks (id, episode_id, stage) VALUES (?, ?, ?)",
                    (str(uuid.uuid4()), episode_id, stage.value),
                )
            conn.commit()
        finally:
            conn.close()
