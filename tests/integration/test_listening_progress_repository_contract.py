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

"""Spec #90 — shared contract for the listening progress repositories.

Every behaviour is asserted identically on SQLite and Postgres: the write
ordering predicate, the unfinished-row listings, the cascades, and the
``listening`` field the inbox JOIN adds. Postgres cases are skipped (not
failed) when no server is reachable; point ``TEST_DATABASE_URL`` at a
scratch database to include them:

    TEST_DATABASE_URL=postgresql://localhost/thestill_listening_test \\
        ./venv/bin/python -m pytest tests/integration/test_listening_progress_repository_contract.py
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from thestill.models.inbox import InboxEntry
from thestill.models.listening import ListeningProgress
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_listening_progress_repository import SqliteListeningProgressRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.sqlite_ext import connect

PG_DSN = os.getenv("TEST_DATABASE_URL", "")

BASE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
MISSING_ID = "00000000-0000-0000-0000-000000000000"


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


class _Env:
    """Backend harness: same seeding API on both engines (uuid ids)."""

    is_pg = False

    def _execute(self, sql: str, params: tuple) -> None:
        raise NotImplementedError

    def _ts(self, dt: datetime):
        return dt if self.is_pg else dt.isoformat()

    def add_user(self) -> str:
        user_id = str(uuid.uuid4())
        self._execute(
            "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)",
            (user_id, f"u-{user_id[:8]}@example.com", self._ts(BASE)),
        )
        return user_id

    def add_episode(self) -> str:
        podcast_id, episode_id = str(uuid.uuid4()), str(uuid.uuid4())
        self._execute(
            "INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug, description, language)"
            " VALUES (?, ?, ?, ?, ?, ?, '', 'en')",
            (podcast_id, self._ts(BASE), self._ts(BASE), f"https://x.test/{podcast_id}.xml", "P", podcast_id[:8]),
        )
        self._execute(
            "INSERT INTO episodes (id, podcast_id, created_at, updated_at, external_id, title, audio_url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (episode_id, podcast_id, self._ts(BASE), self._ts(BASE), episode_id, "E", "https://x.test/e.mp3"),
        )
        return episode_id

    def delete(self, table: str, row_id: str) -> None:
        self._execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


class _SqliteEnv(_Env):
    def __init__(self, db_path: str):
        SqlitePodcastRepository(db_path)  # creates the full schema
        self.db_path = db_path
        self.repo = SqliteListeningProgressRepository(db_path)
        self.inbox = SqliteInboxRepository(db_path)
        self.integrity_error = sqlite3.IntegrityError

    def _execute(self, sql: str, params: tuple) -> None:
        # The project connection: foreign keys on, sqlite-vec loaded (episode
        # deletes fire vec0 triggers).
        with connect(self.db_path, load_vec="soft") as conn:
            conn.execute(sql, params)


class _PostgresEnv(_Env):
    is_pg = True

    def __init__(self, dsn: str):
        import psycopg

        from thestill.repositories.postgres_inbox_repository import PostgresInboxRepository
        from thestill.repositories.postgres_listening_progress_repository import PostgresListeningProgressRepository

        self._psycopg = psycopg
        self.dsn = dsn
        self.repo = PostgresListeningProgressRepository(dsn)
        self.inbox = PostgresInboxRepository(dsn)
        self.integrity_error = psycopg.errors.IntegrityError

    def _execute(self, sql: str, params: tuple) -> None:
        with self._psycopg.connect(self.dsn) as conn:
            conn.execute(sql.replace("?", "%s"), params)


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        yield _SqliteEnv(str(tmp_path / "listening.db"))
        return
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE user_episode_progress, user_episode_inbox, episodes, podcasts, users CASCADE")
    yield _PostgresEnv(PG_DSN)


def _row(
    user_id: str,
    episode_id: str,
    *,
    position: float = 120.0,
    duration: Optional[float] = 3600.0,
    session: str = "tab-a",
    seq: int = 1,
    updated_at: datetime = BASE,
    completed_at: Optional[datetime] = None,
    stopped_at: Optional[datetime] = None,
) -> ListeningProgress:
    return ListeningProgress(
        user_id=user_id,
        episode_id=episode_id,
        position_seconds=position,
        duration_seconds=duration,
        completed_at=completed_at,
        stopped_at=stopped_at,
        session_id=session,
        seq=seq,
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# get / upsert_if_newer — write ordering
# ---------------------------------------------------------------------------
def test_get_missing_returns_none(env):
    user = env.add_user()
    assert env.repo.get(user, MISSING_ID) is None


def test_first_write_inserts_and_round_trips(env):
    user, ep = env.add_user(), env.add_episode()
    assert env.repo.upsert_if_newer(_row(user, ep, position=93.5, seq=4))

    stored = env.repo.get(user, ep)
    assert stored.position_seconds == 93.5
    assert stored.duration_seconds == 3600.0
    assert stored.session_id == "tab-a" and stored.seq == 4
    assert stored.updated_at == BASE
    assert stored.updated_at.tzinfo is not None


def test_same_session_older_or_equal_seq_is_rejected(env):
    user, ep = env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(user, ep, position=500.0, seq=10))

    assert not env.repo.upsert_if_newer(_row(user, ep, position=400.0, seq=9))
    assert not env.repo.upsert_if_newer(_row(user, ep, position=400.0, seq=10))
    assert env.repo.get(user, ep).position_seconds == 500.0


def test_same_session_higher_seq_is_accepted(env):
    user, ep = env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(user, ep, position=500.0, seq=10))

    assert env.repo.upsert_if_newer(_row(user, ep, position=515.0, seq=11))
    assert env.repo.get(user, ep).position_seconds == 515.0


def test_other_session_wins_regardless_of_seq(env):
    """Another tab or device: the latest action wins, no clock comparison."""
    user, ep = env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(user, ep, position=500.0, seq=10))

    assert env.repo.upsert_if_newer(_row(user, ep, position=60.0, session="phone", seq=0))
    stored = env.repo.get(user, ep)
    assert stored.position_seconds == 60.0 and stored.session_id == "phone"


def test_force_overrides_a_higher_stored_seq_from_the_same_session(env):
    user, ep = env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(user, ep, position=500.0, seq=10))

    assert env.repo.upsert_if_newer(_row(user, ep, position=0.0, seq=1, completed_at=BASE), force=True)
    stored = env.repo.get(user, ep)
    assert stored.position_seconds == 0.0 and stored.completed_at == BASE


def test_write_without_duration_keeps_the_stored_one(env):
    user, ep = env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(user, ep, duration=3600.0, seq=1))
    env.repo.upsert_if_newer(_row(user, ep, duration=None, seq=2))
    assert env.repo.get(user, ep).duration_seconds == 3600.0


def test_check_constraints(env):
    user, ep = env.add_user(), env.add_episode()
    with pytest.raises(env.integrity_error):
        env.repo.upsert_if_newer(_row(user, ep, position=-1.0))
    with pytest.raises(env.integrity_error):
        env.repo.upsert_if_newer(_row(user, ep, duration=0.0))


def test_malformed_ids_miss_quietly(env):
    user = env.add_user()
    assert env.repo.get(user, "not-a-uuid") is None
    assert env.repo.list_unfinished("not-a-uuid", limit=5, exclude_stopped=True) == []


# ---------------------------------------------------------------------------
# list_unfinished
# ---------------------------------------------------------------------------
def test_list_unfinished_orders_newest_first_and_filters(env):
    user = env.add_user()
    old, new, done, zero, stopped = (env.add_episode() for _ in range(5))
    env.repo.upsert_if_newer(_row(user, old, updated_at=BASE - timedelta(days=400)))
    env.repo.upsert_if_newer(_row(user, new, updated_at=BASE))
    env.repo.upsert_if_newer(_row(user, done, position=0.0, completed_at=BASE, updated_at=BASE + timedelta(hours=1)))
    env.repo.upsert_if_newer(_row(user, zero, position=0.0, updated_at=BASE + timedelta(hours=2)))
    env.repo.upsert_if_newer(_row(user, stopped, stopped_at=BASE, updated_at=BASE + timedelta(hours=3)))

    restore = env.repo.list_unfinished(user, limit=10, exclude_stopped=True)
    assert [r.episode_id for r in restore] == [new, old]  # no age limit

    strip = env.repo.list_unfinished(user, limit=10, exclude_stopped=False)
    assert [r.episode_id for r in strip] == [stopped, new, old]

    assert [r.episode_id for r in env.repo.list_unfinished(user, limit=1, exclude_stopped=True)] == [new]


def test_list_unfinished_updated_since_keeps_only_recent_rows(env):
    user = env.add_user()
    stale, boundary, fresh = (env.add_episode() for _ in range(3))
    since = BASE - timedelta(days=3)
    env.repo.upsert_if_newer(_row(user, stale, updated_at=since - timedelta(seconds=1)))
    env.repo.upsert_if_newer(_row(user, boundary, updated_at=since))
    env.repo.upsert_if_newer(_row(user, fresh, updated_at=BASE))

    recent = env.repo.list_unfinished(user, limit=10, exclude_stopped=False, updated_since=since)
    assert [r.episode_id for r in recent] == [fresh, boundary]
    # No window: every unfinished row.
    assert len(env.repo.list_unfinished(user, limit=10, exclude_stopped=False)) == 3


def test_list_unfinished_is_per_user(env):
    alice, bob, ep = env.add_user(), env.add_user(), env.add_episode()
    env.repo.upsert_if_newer(_row(alice, ep))
    assert env.repo.list_unfinished(bob, limit=5, exclude_stopped=False) == []


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------
def test_rows_cascade_with_user_and_episode(env):
    alice, bob = env.add_user(), env.add_user()
    ep1, ep2 = env.add_episode(), env.add_episode()
    env.repo.upsert_if_newer(_row(alice, ep1))
    env.repo.upsert_if_newer(_row(bob, ep2))

    env.delete("users", alice)
    env.delete("episodes", ep2)

    assert env.repo.get(alice, ep1) is None
    assert env.repo.get(bob, ep2) is None


# ---------------------------------------------------------------------------
# Inbox JOIN — ``listening`` on inbox items
# ---------------------------------------------------------------------------
def test_inbox_items_carry_listening_or_none(env):
    user = env.add_user()
    played, untouched = env.add_episode(), env.add_episode()
    env.inbox.insert_many(
        [
            InboxEntry(user_id=user, episode_id=played, source="follow_new", delivered_at=BASE),
            InboxEntry(user_id=user, episode_id=untouched, source="follow_new", delivered_at=BASE),
        ]
    )
    env.repo.upsert_if_newer(_row(user, played, position=0.0, completed_at=BASE, updated_at=BASE))

    by_episode = {item.episode.id: item for item in env.inbox.list_items(user)}
    assert by_episode[untouched].listening is None
    listening = by_episode[played].listening
    assert listening.position_seconds == 0.0
    assert listening.completed_at == BASE
    assert listening.updated_at == BASE

    assert env.inbox.get_item(user, played).listening.completed_at == BASE


def test_inbox_listening_is_the_inbox_owners_not_another_users(env):
    alice, bob, ep = env.add_user(), env.add_user(), env.add_episode()
    env.inbox.insert_many([InboxEntry(user_id=alice, episode_id=ep, source="follow_new", delivered_at=BASE)])
    env.repo.upsert_if_newer(_row(bob, ep))

    (item,) = env.inbox.list_items(alice)
    assert item.listening is None


# ---------------------------------------------------------------------------
# listened_at (spec #103) — only moves forward
# ---------------------------------------------------------------------------
def test_listened_at_round_trips_and_never_rewinds(env):
    user, ep = env.add_user(), env.add_episode()
    later = BASE + timedelta(hours=1)
    first = _row(user, ep, seq=1).model_copy(update={"listened_at": later})
    assert env.repo.upsert_if_newer(first)
    assert env.repo.get(user, ep).listened_at == later

    # A write planned from a stale read carries an older value, or none.
    for stale in (BASE, None):
        assert env.repo.upsert_if_newer(_row(user, ep, seq=2 if stale else 3).model_copy(update={"listened_at": stale}))
        assert env.repo.get(user, ep).listened_at == later

    newest = later + timedelta(hours=1)
    assert env.repo.upsert_if_newer(_row(user, ep, seq=4).model_copy(update={"listened_at": newest}))
    assert env.repo.get(user, ep).listened_at == newest
