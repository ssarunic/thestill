"""Dual-backend contract for ``user_briefings.opened_at`` (spec #95 Phase 0).

Postgres cases skip (not fail) when ``TEST_DATABASE_URL`` is unset or
unreachable.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing import Briefing
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)


@pytest.fixture(params=["sqlite", "postgres"])
def briefings(request, tmp_path):
    """Yield ``(briefing_repo, make_briefing)`` on a clean database."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the DDL
        user_repo, repo = SqliteUserRepository(db_path=db), SqliteBriefingRepository(db_path=db)
    else:
        if not PG_OK:
            pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
        import psycopg

        from thestill.repositories.postgres_briefing_repository import PostgresBriefingRepository
        from thestill.repositories.postgres_schema import ensure_schema
        from thestill.repositories.postgres_user_repository import PostgresUserRepository

        ensure_schema(PG_DSN)
        with psycopg.connect(PG_DSN) as conn:
            conn.execute("TRUNCATE users, user_briefings CASCADE")
        user_repo, repo = PostgresUserRepository(PG_DSN), PostgresBriefingRepository(PG_DSN)

    def make_briefing(**overrides) -> Briefing:
        user = user_repo.save(User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:10]}@example.com"))
        fields = dict(user_id=user.id, cursor_from=NOW - timedelta(days=1), cursor_to=NOW, episode_count=1)
        fields.update(overrides)
        return repo.insert(Briefing(**fields))

    yield repo, make_briefing


def test_mark_opened_keeps_the_first_time(briefings):
    repo, make_briefing = briefings
    briefing = make_briefing()
    assert repo.get(briefing.id).opened_at is None

    first = repo.mark_opened(briefing.id, NOW)
    assert first.opened_at == NOW
    again = repo.mark_opened(briefing.id, NOW + timedelta(hours=3))
    assert again.opened_at == NOW
    assert repo.get(briefing.id).opened_at == NOW


def test_mark_opened_unknown_id_returns_none(briefings):
    repo, _ = briefings
    assert repo.mark_opened(str(uuid.uuid4()), NOW) is None


def test_listening_implies_opening(briefings):
    repo, make_briefing = briefings
    fresh = make_briefing()
    assert repo.update_listened_at(fresh.id, NOW).opened_at == NOW

    opened_earlier = make_briefing()
    repo.mark_opened(opened_earlier.id, NOW - timedelta(hours=5))
    listened = repo.update_listened_at(opened_earlier.id, NOW)
    assert listened.listened_at == NOW
    assert listened.opened_at == NOW - timedelta(hours=5)


def test_insert_round_trips_opened_at(briefings):
    repo, make_briefing = briefings
    briefing = make_briefing(opened_at=NOW)
    assert repo.get(briefing.id).opened_at == NOW


def test_sqlite_legacy_database_gains_columns_and_backfills(tmp_path):
    """A database from before spec #95: the columns are added on open and
    briefings already marked listened count as opened."""
    db = str(tmp_path / "legacy.db")
    user_id = str(uuid.uuid4())
    with sqlite3.connect(db) as conn:
        # The two tables as they were before spec #95.
        conn.executescript(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY NOT NULL, email TEXT NOT NULL UNIQUE, name TEXT NULL,
                picture TEXT NULL, google_id TEXT UNIQUE,
                created_at TIMESTAMP NOT NULL, last_login_at TIMESTAMP NULL,
                region TEXT NULL, region_locked INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE user_briefings (
                id TEXT PRIMARY KEY NOT NULL, user_id TEXT NOT NULL,
                cursor_from TIMESTAMP NOT NULL, cursor_to TIMESTAMP NOT NULL,
                episode_count INTEGER NOT NULL, script_path TEXT NULL, audio_path TEXT NULL,
                created_at TIMESTAMP NOT NULL, listened_at TIMESTAMP NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES (?, 'a@example.com', ?)", (user_id, NOW.isoformat())
        )
        for listened in (NOW.isoformat(), None):
            conn.execute(
                "INSERT INTO user_briefings (id, user_id, cursor_from, cursor_to, episode_count, created_at, listened_at)"
                " VALUES (?, ?, ?, ?, 1, ?, ?)",
                (
                    str(uuid.uuid4()),
                    user_id,
                    (NOW - timedelta(days=1)).isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                    listened,
                ),
            )

    SqlitePodcastRepository(db_path=db)  # runs the migrations

    with sqlite3.connect(db) as conn:
        assert "signup_source" in {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        rows = conn.execute("SELECT listened_at, opened_at FROM user_briefings ORDER BY listened_at").fetchall()
    assert sorted(rows, key=lambda r: r[0] or "") == [(None, None), (NOW.isoformat(), NOW.isoformat())]


@pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")
def test_alembic_0016_backfills_opened_at_from_listened_at():
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema, run_alembic_upgrade

    ensure_schema(PG_DSN)
    user_id = str(uuid.uuid4())
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, user_briefings CASCADE")
        conn.execute("INSERT INTO users (id, email) VALUES (%s, 'b@example.com')", (user_id,))
        for listened in (NOW, None):
            conn.execute(
                "INSERT INTO user_briefings (id, user_id, cursor_from, cursor_to, episode_count, listened_at)"
                " VALUES (%s, %s, %s, %s, 1, %s)",
                (str(uuid.uuid4()), user_id, NOW - timedelta(days=1), NOW, listened),
            )
        conn.execute("UPDATE user_briefings SET opened_at = NULL")
        # ensure_schema is head-equivalent; pretend 0015 so only 0016 runs.
        conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)")
        conn.execute("DELETE FROM alembic_version")
        conn.execute("INSERT INTO alembic_version VALUES ('0015')")

    run_alembic_upgrade(PG_DSN)

    with psycopg.connect(PG_DSN) as conn:
        rows = conn.execute("SELECT listened_at, opened_at FROM user_briefings").fetchall()
    assert sorted(rows, key=lambda r: r[0] is None) == [(NOW, NOW), (None, None)]
