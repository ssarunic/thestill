# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Spec #103 Phase 1 — ``user_episode_views`` on SQLite and Postgres, and
the 0019 backfills. Postgres cases skip without ``TEST_DATABASE_URL``."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.repositories.sqlite_episode_view_repository import SqliteEpisodeViewRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.utils.sqlite_ext import connect

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
MISSING_ID = "00000000-0000-4000-8000-000000000000"


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
pg_only = pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")


class _Env:
    is_pg = False

    def ts(self, dt):
        return dt if self.is_pg else dt.isoformat()

    def seed(self, *, opted_in=True):
        user_id, podcast_id, episode_id = (str(uuid.uuid4()) for _ in range(3))
        self.execute(
            "INSERT INTO users (id, email, created_at, usage_analytics_enabled) VALUES (?, ?, ?, ?)",
            (user_id, f"{user_id[:8]}@example.com", self.ts(NOW), opted_in),
        )
        self.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, description, language) VALUES (?, ?, 'P', ?, '', 'en')",
            (podcast_id, f"https://x.test/{podcast_id}.xml", podcast_id[:8]),
        )
        self.execute(
            "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url) VALUES (?, ?, ?, 'E', 'https://x/e')",
            (episode_id, podcast_id, episode_id),
        )
        return user_id, episode_id


class _SqliteEnv(_Env):
    def __init__(self, db_path):
        SqlitePodcastRepository(db_path)  # creates the schema
        self.db_path = db_path
        self.repo = SqliteEpisodeViewRepository(db_path)
        self.users = SqliteUserRepository(db_path)

    def execute(self, sql, params=()):
        with connect(self.db_path, load_vec="soft") as conn:
            return conn.execute(sql, params).fetchall()

    def views(self):
        return [tuple(r) for r in self.execute("SELECT user_id, episode_id, first_viewed_at FROM user_episode_views")]


class _PostgresEnv(_Env):
    is_pg = True

    def __init__(self, dsn):
        from thestill.repositories.postgres_episode_view_repository import PostgresEpisodeViewRepository
        from thestill.repositories.postgres_user_repository import PostgresUserRepository

        self.dsn = dsn
        self.repo = PostgresEpisodeViewRepository(dsn)
        self.users = PostgresUserRepository(dsn)

    def execute(self, sql, params=()):
        import psycopg

        with psycopg.connect(self.dsn) as conn:
            cursor = conn.execute(sql.replace("?", "%s"), params)
            return cursor.fetchall() if cursor.description else []

    def views(self):
        rows = self.execute("SELECT user_id::text, episode_id::text, first_viewed_at FROM user_episode_views")
        return [(u, e, at.astimezone(timezone.utc).isoformat()) for u, e, at in rows]


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        yield _SqliteEnv(str(tmp_path / "views.db"))
        return
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE user_episode_views, episodes, podcasts, users CASCADE")
    yield _PostgresEnv(PG_DSN)


def test_first_view_is_kept(env):
    user, ep = env.seed()
    assert env.repo.record_view(user, ep, NOW) is True
    assert env.repo.record_view(user, ep, NOW + timedelta(days=1)) is False
    assert env.views() == [(user, ep, NOW.isoformat())]


def test_unknown_or_malformed_episode_stores_nothing(env):
    user, _ = env.seed()
    assert env.repo.record_view(user, MISSING_ID, NOW) is False
    assert env.repo.record_view(user, "not-a-uuid", NOW) is False
    assert env.views() == []


def test_opting_out_erases_views_and_listened_at(env):
    """Turning usage analytics off removes what exists only to measure; the
    position and completion stay, and other readers are untouched."""
    leaver, ep = env.seed()
    stayer, other_ep = env.seed()
    for user, episode in ((leaver, ep), (stayer, other_ep)):
        env.repo.record_view(user, episode, NOW)
        env.execute(
            "INSERT INTO user_episode_progress (user_id, episode_id, position_seconds, session_id, seq, updated_at,"
            " completed_at, listened_at) VALUES (?, ?, 0, 's', 1, ?, ?, ?)",
            (user, episode, env.ts(NOW), env.ts(NOW), env.ts(NOW)),
        )

    assert env.users.update_usage_analytics(leaver, False) is True

    assert [v[0] for v in env.views()] == [stayer]
    rows = {
        str(u): (completed is not None, listened is not None)
        for u, completed, listened in env.execute(
            "SELECT user_id, completed_at, listened_at FROM user_episode_progress"
        )
    }
    assert rows == {leaver: (True, False), stayer: (True, True)}
    env.users.update_usage_analytics(leaver, True)  # turning it back on restores nothing
    assert [v[0] for v in env.views()] == [stayer]


# ----- backfills -----------------------------------------------------------


def _inbox_and_progress(env, user, ep, *, state, position, completed_at=None):
    env.execute(
        "INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at, state_changed_at)"
        " VALUES (?, ?, ?, 'follow_new', ?, ?, ?)",
        (str(uuid.uuid4()), user, ep, state, env.ts(NOW - timedelta(days=1)), env.ts(NOW)),
    )
    env.execute(
        "INSERT INTO user_episode_progress (user_id, episode_id, position_seconds, session_id, seq, updated_at,"
        " completed_at) VALUES (?, ?, ?, 's', 1, ?, ?)",
        (user, ep, position, env.ts(NOW), env.ts(completed_at) if completed_at else None),
    )


def test_sqlite_legacy_database_backfills_opted_in_users_only(tmp_path):
    """A database from before spec #103: opted-in reads become views and
    real listens get listened_at; an opted-out user's history is skipped."""
    db = str(tmp_path / "legacy.db")
    env = _SqliteEnv(db)
    with connect(db, load_vec="soft") as conn:  # back to the pre-#103 shape (triggers need vec0)
        conn.execute("DROP TABLE user_episode_views")
        conn.execute("ALTER TABLE user_episode_progress DROP COLUMN listened_at")
    rows = {}
    for name, opted_in, state, position in (
        ("in_read", True, "read", 120.0),
        ("in_saved", True, "saved", 0.0),  # a reset: no listen
        ("out_read", False, "read", 120.0),
    ):
        user, ep = env.seed(opted_in=opted_in)
        _inbox_and_progress(env, user, ep, state=state, position=position)
        rows[name] = (user, ep)

    SqlitePodcastRepository(db_path=db)  # runs the migrations

    assert env.views() == [(*rows["in_read"], NOW.isoformat())]
    listened = dict(
        ((u, e), at) for u, e, at in env.execute("SELECT user_id, episode_id, listened_at FROM user_episode_progress")
    )
    assert listened == {rows["in_read"]: NOW.isoformat(), rows["in_saved"]: None, rows["out_read"]: None}


@pg_only
def test_alembic_0019_backfills_opted_in_users_only():
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema, run_alembic_upgrade

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE user_episode_views, episodes, podcasts, users CASCADE")
    env = _PostgresEnv(PG_DSN)
    rows = {}
    for name, opted_in, state, position, completed in (
        ("in_read", True, "read", 120.0, None),
        ("in_played", True, "dismissed", 0.0, NOW),  # completed at position 0: a listen under Phase 0
        ("in_reset", True, "saved", 0.0, None),
        ("out_read", False, "read", 120.0, None),
    ):
        user, ep = env.seed(opted_in=opted_in)
        _inbox_and_progress(env, user, ep, state=state, position=position, completed_at=completed)
        rows[name] = (user, ep)
    with psycopg.connect(PG_DSN) as conn:
        # ensure_schema is head-equivalent; pretend 0018 so only 0019 runs.
        conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)")
        conn.execute("DELETE FROM alembic_version")
        conn.execute("INSERT INTO alembic_version VALUES ('0018')")

    run_alembic_upgrade(PG_DSN)

    assert env.views() == [(*rows["in_read"], NOW.isoformat())]
    listened = {
        (u, e): at
        for u, e, at in env.execute("SELECT user_id::text, episode_id::text, listened_at FROM user_episode_progress")
    }
    assert listened == {rows["in_read"]: NOW, rows["in_played"]: NOW, rows["in_reset"]: None, rows["out_read"]: None}
