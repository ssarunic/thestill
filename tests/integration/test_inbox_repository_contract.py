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

"""Dual-backend contract suite for the inbox repository (spec #44).

The same tests run against BOTH the SQLite and the PostgreSQL implementations
behind the ``InboxRepository`` ABC. This is the fidelity guarantee spec #42
FM-5 demands: the Postgres port is exercised against a *real* Postgres, never a
mock, and every behaviour is asserted identically on both engines so a dialect
divergence fails the build.

The Postgres cases are skipped (not failed) when no server is reachable, so the
suite still passes on a SQLite-only CI runner. Point ``TEST_DATABASE_URL`` at a
Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_inbox \\
        ./venv/bin/python -m pytest tests/integration/test_inbox_repository_contract.py
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from thestill.models.inbox import InboxEntry
from thestill.models.podcast import Podcast
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


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


# ---------------------------------------------------------------------------
# Backend harnesses — same seeding API on both engines so the test bodies
# stay backend-agnostic. Parent rows (users/podcasts/episodes/followers) are
# inserted with production-shaped values: uuid ids, tz-aware datetimes.
# ---------------------------------------------------------------------------
class _SqliteEnv:
    """SQLite backend: podcast repo owns the DDL; parents seeded via models/SQL."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.podcast_repo = SqlitePodcastRepository(db_path)  # creates full schema
        self.repo = SqliteInboxRepository(db_path)
        self.integrity_error = sqlite3.IntegrityError

    def add_user(self, email: str) -> str:
        user_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)",
                (user_id, email, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        return user_id

    def add_podcast(self, slug: str) -> str:
        podcast = Podcast(
            id=str(uuid.uuid4()),
            title=f"Podcast {slug}",
            slug=slug,
            rss_url=f"https://example.com/{slug}.xml",
            description="A test podcast",
            image_url="https://cdn.example.com/cover.jpg",
        )
        self.podcast_repo.save(podcast)
        return podcast.id

    def add_episode(
        self,
        podcast_id: str,
        title: str,
        *,
        published_at: Optional[datetime] = None,
        pub_date: Optional[datetime] = None,
    ) -> str:
        episode_id = str(uuid.uuid4())
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    id, podcast_id, external_id, title, slug, description,
                    description_html, audio_url, published_at, pub_date
                ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """,
                (
                    episode_id,
                    podcast_id,
                    f"ext-{title}",
                    title,
                    "",
                    "Episode description",
                    f"https://cdn.example.com/{title}.mp3",
                    published_at.isoformat() if published_at else None,
                    pub_date.isoformat() if pub_date else None,
                ),
            )
            conn.commit()
        return episode_id

    def add_follower(self, user_id: str, podcast_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), user_id, podcast_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()


class _PostgresEnv:
    """Postgres backend: typed schema bootstrap; parents seeded via direct SQL."""

    def __init__(self, dsn: str):
        import psycopg

        from thestill.repositories.postgres_inbox_repository import PostgresInboxRepository

        self._psycopg = psycopg
        self.dsn = dsn
        self.repo = PostgresInboxRepository(dsn)
        self.integrity_error = psycopg.errors.IntegrityError

    def _execute(self, sql: str, params: tuple) -> None:
        with self._psycopg.connect(self.dsn) as conn:
            conn.execute(sql, params)

    def add_user(self, email: str) -> str:
        user_id = str(uuid.uuid4())
        self._execute("INSERT INTO users (id, email) VALUES (%s, %s)", (user_id, email))
        return user_id

    def add_podcast(self, slug: str) -> str:
        podcast_id = str(uuid.uuid4())
        self._execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, image_url) VALUES (%s, %s, %s, %s, %s)",
            (podcast_id, f"https://example.com/{slug}.xml", f"Podcast {slug}", slug, "https://cdn.example.com/cover.jpg"),
        )
        return podcast_id

    def add_episode(
        self,
        podcast_id: str,
        title: str,
        *,
        published_at: Optional[datetime] = None,
        pub_date: Optional[datetime] = None,
    ) -> str:
        episode_id = str(uuid.uuid4())
        self._execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url, published_at, pub_date
            ) VALUES (%s, %s, %s, %s, %s, %s, '', %s, %s, %s)
            """,
            (
                episode_id,
                podcast_id,
                f"ext-{title}",
                title,
                "",
                "Episode description",
                f"https://cdn.example.com/{title}.mp3",
                published_at,
                pub_date,
            ),
        )
        return episode_id

    def add_follower(self, user_id: str, podcast_id: str) -> None:
        self._execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id) VALUES (%s, %s, %s)",
            (str(uuid.uuid4()), user_id, podcast_id),
        )


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    """Yield a clean inbox backend harness for each engine."""
    if request.param == "sqlite":
        yield _SqliteEnv(str(tmp_path / "contract.db"))
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)  # idempotent typed-schema bootstrap
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE user_episode_inbox, podcast_followers, episodes, podcasts, users CASCADE")
    yield _PostgresEnv(PG_DSN)


MISSING_ID = "00000000-0000-0000-0000-000000000000"
BASE = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _entry(user_id: str, episode_id: str, **overrides) -> InboxEntry:
    base = dict(user_id=user_id, episode_id=episode_id, source="follow_new")
    base.update(overrides)
    return InboxEntry(**base)


# ---------------------------------------------------------------------------
# insert_many + get
# ---------------------------------------------------------------------------
def test_insert_many_inserts_rows_and_returns_count(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep1 = env.add_episode(podcast, "ep1")
    ep2 = env.add_episode(podcast, "ep2")

    assert env.repo.insert_many([_entry(user, ep1), _entry(user, ep2)]) == 2

    fetched = env.repo.get(user, ep1)
    assert fetched is not None
    assert fetched.user_id == user
    assert fetched.episode_id == ep1
    assert fetched.source == "follow_new"
    assert fetched.state == "unread"
    assert fetched.state_changed_at is None
    assert fetched.delivered_at.tzinfo is not None  # tz-aware on both engines


def test_insert_many_dedups_on_user_episode_pair(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")

    assert env.repo.insert_many([_entry(user, ep, source="follow_new")]) == 1
    # Re-insert the same pair (different uuid, different source); no-op.
    assert env.repo.insert_many([_entry(user, ep, source="follow_seed")]) == 0

    # The original row's provenance is preserved.
    entry = env.repo.get(user, ep)
    assert entry is not None
    assert entry.source == "follow_new"


def test_insert_many_partial_batch_counts_only_new_rows(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep1 = env.add_episode(podcast, "ep1")
    ep2 = env.add_episode(podcast, "ep2")

    env.repo.insert_many([_entry(user, ep1)])
    # Batch with one existing + one new pair → only the new row counts.
    assert env.repo.insert_many([_entry(user, ep1), _entry(user, ep2)]) == 1


def test_insert_many_empty_list_returns_zero(env):
    assert env.repo.insert_many([]) == 0


def test_insert_many_rejects_invalid_source_at_db_layer(env):
    """Pydantic guards model construction; if a caller bypasses it via
    ``model_construct``, the CHECK constraint catches the bad value on
    both engines."""
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")

    bogus = InboxEntry.model_construct(
        id=str(uuid.uuid4()),
        user_id=user,
        episode_id=ep,
        source="bogus",
        state="unread",
        delivered_at=datetime.now(timezone.utc),
        state_changed_at=None,
    )
    with pytest.raises(env.integrity_error):
        env.repo.insert_many([bogus])


def test_get_missing_pair_returns_none(env):
    user = env.add_user("alice@example.com")
    assert env.repo.get(user, MISSING_ID) is None


def test_delivered_at_roundtrips_exactly(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")
    delivered = datetime(2026, 3, 4, 5, 6, 7, 123456, tzinfo=timezone.utc)

    env.repo.insert_many([_entry(user, ep, delivered_at=delivered)])
    got = env.repo.get(user, ep)
    # tz-aware instant equality (not string comparison) — microsecond fidelity.
    assert got.delivered_at == delivered


# ---------------------------------------------------------------------------
# find_or_create
# ---------------------------------------------------------------------------
def test_find_or_create_creates_then_finds(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")

    entry, created = env.repo.find_or_create(user_id=user, episode_id=ep, source="import")
    assert created is True
    assert entry.source == "import"
    assert entry.state == "unread"

    again, created_again = env.repo.find_or_create(user_id=user, episode_id=ep, source="ad_hoc")
    assert created_again is False
    assert again.id == entry.id  # identity stays with the original row
    assert again.source == "import"  # provenance never rewritten


# ---------------------------------------------------------------------------
# update_state
# ---------------------------------------------------------------------------
def test_update_state_sets_state_and_changed_at(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")
    env.repo.insert_many([_entry(user, ep)])

    now = datetime.now(timezone.utc)
    entry = env.repo.update_state(user, ep, "read", now)

    assert entry is not None
    assert entry.state == "read"
    assert entry.state_changed_at == now


def test_update_state_transitions_read_saved_dismissed(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")
    env.repo.insert_many([_entry(user, ep)])

    for state in ("read", "saved", "dismissed", "unread"):
        entry = env.repo.update_state(user, ep, state, datetime.now(timezone.utc))
        assert entry is not None
        assert entry.state == state
        assert env.repo.get(user, ep).state == state


def test_update_state_returns_none_when_no_row(env):
    user = env.add_user("alice@example.com")
    assert env.repo.update_state(user, MISSING_ID, "read", datetime.now(timezone.utc)) is None


def test_update_state_invalid_state_hits_db_check(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "ep1")
    env.repo.insert_many([_entry(user, ep)])

    with pytest.raises(env.integrity_error):
        env.repo.update_state(user, ep, "archived", datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# list_items (hydrated JOIN view)
# ---------------------------------------------------------------------------
def test_list_items_orders_newest_first_and_excludes_dismissed_by_default(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep_older = env.add_episode(podcast, "older")
    ep_newer = env.add_episode(podcast, "newer")
    ep_dismissed = env.add_episode(podcast, "dismissed")

    env.repo.insert_many(
        [
            _entry(user, ep_older, delivered_at=BASE),
            _entry(user, ep_newer, delivered_at=BASE + timedelta(hours=1)),
            _entry(user, ep_dismissed, delivered_at=BASE + timedelta(hours=2)),
        ]
    )
    env.repo.update_state(user, ep_dismissed, "dismissed", datetime.now(timezone.utc))

    items = env.repo.list_items(user)
    assert [item.episode.title for item in items] == ["newer", "older"]
    # Composed view exposes podcast metadata too.
    assert items[0].podcast.slug == "p1"
    assert items[0].podcast.title == "Podcast p1"
    assert items[0].podcast.image_url == "https://cdn.example.com/cover.jpg"


def test_list_items_hydrates_episode_and_entry(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    pub = datetime(2026, 2, 1, 9, 0, tzinfo=timezone.utc)
    ep = env.add_episode(podcast, "ep1", pub_date=pub, published_at=pub + timedelta(days=1))
    env.repo.insert_many([_entry(user, ep, source="ad_hoc", delivered_at=BASE)])

    (item,) = env.repo.list_items(user)
    assert item.entry.user_id == user
    assert item.entry.episode_id == ep
    assert item.entry.source == "ad_hoc"
    assert item.entry.delivered_at == BASE
    assert item.episode.id == ep
    assert item.episode.podcast_id == podcast
    assert item.episode.external_id == "ext-ep1"
    assert str(item.episode.audio_url) == "https://cdn.example.com/ep1.mp3"
    assert item.episode.pub_date == pub
    assert item.episode.published_at == pub + timedelta(days=1)


def test_list_items_state_filter_and_dismissed_surfacing(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep1 = env.add_episode(podcast, "ep1")
    ep2 = env.add_episode(podcast, "ep2")
    ep3 = env.add_episode(podcast, "ep3")
    env.repo.insert_many([_entry(user, ep1), _entry(user, ep2), _entry(user, ep3)])
    now = datetime.now(timezone.utc)
    env.repo.update_state(user, ep2, "saved", now)
    env.repo.update_state(user, ep3, "dismissed", now)

    assert [i.episode.title for i in env.repo.list_items(user, state="saved")] == ["ep2"]
    assert [i.episode.title for i in env.repo.list_items(user, state="unread")] == ["ep1"]
    # dismissed rows are reachable only via the explicit filter.
    assert [i.episode.title for i in env.repo.list_items(user, state="dismissed")] == ["ep3"]


def test_list_items_before_cursor(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep_old = env.add_episode(podcast, "old")
    ep_new = env.add_episode(podcast, "new")

    env.repo.insert_many(
        [
            _entry(user, ep_old, delivered_at=BASE),
            _entry(user, ep_new, delivered_at=BASE + timedelta(hours=1)),
        ]
    )

    older_only = env.repo.list_items(user, before=BASE + timedelta(hours=1))
    assert [i.episode.title for i in older_only] == ["old"]
    # Cursor is strict: a row delivered exactly at ``before`` is excluded.
    assert env.repo.list_items(user, before=BASE) == []


def test_list_items_respects_limit(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    for i in range(3):
        ep = env.add_episode(podcast, f"ep-{i}")
        env.repo.insert_many([_entry(user, ep, delivered_at=BASE + timedelta(hours=i))])

    items = env.repo.list_items(user, limit=2)
    assert [i.episode.title for i in items] == ["ep-2", "ep-1"]
    assert env.repo.list_items(user, limit=0) == []


def test_list_items_does_not_leak_other_users_rows(env):
    alice = env.add_user("alice@example.com")
    bob = env.add_user("bob@example.com")
    podcast = env.add_podcast("p1")
    ep = env.add_episode(podcast, "shared")

    env.repo.insert_many([_entry(alice, ep), _entry(bob, ep)])

    assert len(env.repo.list_items(alice)) == 1
    assert len(env.repo.list_items(bob)) == 1
    env.repo.update_state(alice, ep, "read", datetime.now(timezone.utc))
    # bob still sees it as unread — per-user state on the same episode.
    assert len(env.repo.list_items(bob, state="unread")) == 1


# ---------------------------------------------------------------------------
# unread_count
# ---------------------------------------------------------------------------
def test_unread_count_only_counts_unread(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    eps = [env.add_episode(podcast, f"ep-{i}") for i in range(3)]
    env.repo.insert_many([_entry(user, ep) for ep in eps])

    now = datetime.now(timezone.utc)
    env.repo.update_state(user, eps[1], "read", now)
    env.repo.update_state(user, eps[2], "saved", now)

    assert env.repo.unread_count(user) == 1


def test_unread_count_zero_for_empty_inbox(env):
    user = env.add_user("alice@example.com")
    assert env.repo.unread_count(user) == 0


# ---------------------------------------------------------------------------
# recent_published_episode_ids
# ---------------------------------------------------------------------------
def test_recent_published_episode_ids_orders_by_published_at_desc(env):
    """When pub_date is missing the ordering falls back to published_at."""
    podcast = env.add_podcast("p1")
    older = env.add_episode(podcast, "older", published_at=BASE)
    newer = env.add_episode(podcast, "newer", published_at=BASE + timedelta(days=1))
    env.add_episode(podcast, "unpublished", published_at=None)

    assert env.repo.recent_published_episode_ids(podcast, limit=10) == [newer, older]


def test_recent_published_episode_ids_prefers_pub_date_over_published_at(env):
    """``pub_date`` (RSS air date) drives the ordering, not pipeline-finish time."""
    podcast = env.add_podcast("p1")
    air_jan_1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    air_jan_2 = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)
    pipeline_jan_3 = datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc)
    pipeline_jan_4 = datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc)

    # The episode that aired *first* (Jan 1) was processed *later* (Jan 4).
    older_aired = env.add_episode(podcast, "older-aired", pub_date=air_jan_1, published_at=pipeline_jan_4)
    newer_aired = env.add_episode(podcast, "newer-aired", pub_date=air_jan_2, published_at=pipeline_jan_3)

    assert env.repo.recent_published_episode_ids(podcast, limit=10) == [newer_aired, older_aired]


def test_recent_published_episode_ids_respects_limit_and_zero(env):
    podcast = env.add_podcast("p1")
    for i in range(5):
        env.add_episode(podcast, f"ep-{i}", published_at=BASE + timedelta(days=i))

    assert len(env.repo.recent_published_episode_ids(podcast, limit=2)) == 2
    assert env.repo.recent_published_episode_ids(podcast, limit=0) == []


def test_recent_published_episode_ids_excludes_unpublished(env):
    podcast = env.add_podcast("p1")
    env.add_episode(podcast, "never", published_at=None)
    assert env.repo.recent_published_episode_ids(podcast, limit=10) == []


# ---------------------------------------------------------------------------
# list_episode_ids_in_window (briefing candidate set, spec #36)
# ---------------------------------------------------------------------------
def test_list_episode_ids_in_window_filters_window_and_states(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    eps = [env.add_episode(podcast, f"ep-{i}") for i in range(5)]

    env.repo.insert_many(
        [
            # Inside window, eligible.
            _entry(user, eps[0], source="follow_seed", state="unread", delivered_at=BASE),
            _entry(user, eps[1], source="follow_seed", state="saved", delivered_at=BASE + timedelta(minutes=10)),
            # Inside window, but read/dismissed → excluded by state filter.
            _entry(
                user,
                eps[2],
                source="follow_seed",
                state="read",
                delivered_at=BASE + timedelta(minutes=20),
                state_changed_at=BASE + timedelta(hours=1),
            ),
            _entry(
                user,
                eps[3],
                source="follow_seed",
                state="dismissed",
                delivered_at=BASE + timedelta(minutes=30),
                state_changed_at=BASE + timedelta(hours=1),
            ),
            # Outside window (after ``until``).
            _entry(user, eps[4], source="follow_seed", state="unread", delivered_at=BASE + timedelta(hours=3)),
        ]
    )

    ids = env.repo.list_episode_ids_in_window(user, since=BASE, until=BASE + timedelta(hours=2))
    # Oldest-delivered first.
    assert ids == [eps[0], eps[1]]


def test_list_episode_ids_in_window_boundaries_are_half_open(env):
    """``[since, until)``: a row at ``since`` is in, a row at ``until`` is out."""
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep_at_since = env.add_episode(podcast, "at-since")
    ep_at_until = env.add_episode(podcast, "at-until")
    until = BASE + timedelta(hours=1)

    env.repo.insert_many(
        [
            _entry(user, ep_at_since, delivered_at=BASE),
            _entry(user, ep_at_until, delivered_at=until),
        ]
    )
    assert env.repo.list_episode_ids_in_window(user, since=BASE, until=until) == [ep_at_since]


def test_list_episode_ids_in_window_custom_states(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep_read = env.add_episode(podcast, "read-ep")
    ep_unread = env.add_episode(podcast, "unread-ep")
    env.repo.insert_many(
        [
            _entry(user, ep_read, state="read", delivered_at=BASE, state_changed_at=BASE),
            _entry(user, ep_unread, state="unread", delivered_at=BASE + timedelta(minutes=1)),
        ]
    )

    ids = env.repo.list_episode_ids_in_window(
        user, since=BASE, until=BASE + timedelta(hours=1), states=("read",)
    )
    assert ids == [ep_read]


def test_list_episode_ids_in_window_empty_states_returns_empty(env):
    user = env.add_user("alice@example.com")
    assert env.repo.list_episode_ids_in_window(user, since=BASE, until=BASE + timedelta(hours=1), states=()) == []


# ---------------------------------------------------------------------------
# count_imports_for_user_since (quota plumbing)
# ---------------------------------------------------------------------------
def test_count_imports_only_counts_import_source(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep1 = env.add_episode(podcast, "ep1")
    ep2 = env.add_episode(podcast, "ep2")

    env.repo.insert_many([_entry(user, ep1, source="follow_new"), _entry(user, ep2, source="import")])
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert env.repo.count_imports_for_user_since(user, since) == 1


def test_count_imports_respects_window_inclusive_since(env):
    user = env.add_user("alice@example.com")
    podcast = env.add_podcast("p1")
    ep_old = env.add_episode(podcast, "old")
    ep_edge = env.add_episode(podcast, "edge")
    ep_new = env.add_episode(podcast, "new")

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    env.repo.insert_many(
        [
            _entry(user, ep_old, source="import", delivered_at=now - timedelta(hours=48)),
            _entry(user, ep_edge, source="import", delivered_at=since),  # >= is inclusive
            _entry(user, ep_new, source="import", delivered_at=now - timedelta(minutes=10)),
        ]
    )
    assert env.repo.count_imports_for_user_since(user, since) == 2


def test_count_imports_is_per_user(env):
    alice = env.add_user("alice@example.com")
    bob = env.add_user("bob@example.com")
    podcast = env.add_podcast("p1")
    ep_a = env.add_episode(podcast, "a")
    ep_b = env.add_episode(podcast, "b")

    env.repo.insert_many([_entry(alice, ep_a, source="import"), _entry(bob, ep_b, source="import")])
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert env.repo.count_imports_for_user_since(alice, since) == 1
    assert env.repo.count_imports_for_user_since(bob, since) == 1


# ---------------------------------------------------------------------------
# backfill_existing_followers
# ---------------------------------------------------------------------------
def _seed_backfill_world(env):
    """Two followers of p1 (3 published eps), one follower of p2 (1 ep)."""
    alice = env.add_user("alice@example.com")
    bob = env.add_user("bob@example.com")
    p1 = env.add_podcast("p1")
    p2 = env.add_podcast("p2")
    p1_eps = [env.add_episode(p1, f"p1-ep-{i}", published_at=BASE + timedelta(days=i)) for i in range(3)]
    p2_ep = env.add_episode(p2, "p2-ep", published_at=BASE)
    env.add_episode(p1, "p1-unpublished", published_at=None)  # never seeded
    env.add_follower(alice, p1)
    env.add_follower(bob, p1)
    env.add_follower(alice, p2)
    return alice, bob, p1, p2, p1_eps, p2_ep


def test_backfill_dry_run_matches_real_run_and_is_idempotent(env):
    _seed_backfill_world(env)

    # 2 followers × 2 newest p1 eps + 1 follower × 1 p2 ep = 5 rows.
    assert env.repo.backfill_existing_followers(2, dry_run=True) == 5
    assert env.repo.backfill_existing_followers(2) == 5
    # Second pass: everything already in the inbox → no-op on both counts.
    assert env.repo.backfill_existing_followers(2, dry_run=True) == 0
    assert env.repo.backfill_existing_followers(2) == 0


def test_backfill_seeds_newest_per_podcast_as_unread_follow_seed(env):
    alice, _, _, _, p1_eps, p2_ep = _seed_backfill_world(env)

    env.repo.backfill_existing_followers(2)

    items = env.repo.list_items(alice)
    assert {i.entry.episode_id for i in items} == {p1_eps[1], p1_eps[2], p2_ep}
    assert all(i.entry.source == "follow_seed" for i in items)
    assert all(i.entry.state == "unread" for i in items)
    assert env.repo.unread_count(alice) == 3
    # The oldest p1 episode fell outside limit=2 and was never delivered.
    assert env.repo.get(alice, p1_eps[0]) is None


def test_backfill_delivered_at_stagger_puts_newest_seed_on_top(env):
    alice, _, p1, _, p1_eps, _ = _seed_backfill_world(env)

    env.repo.backfill_existing_followers(3)

    # rn=1 (newest-aired) gets the latest delivered_at, so the inbox view
    # lists seeds newest-aired first.
    titles = [i.episode.title for i in env.repo.list_items(alice) if i.episode.podcast_id == p1]
    assert titles == ["p1-ep-2", "p1-ep-1", "p1-ep-0"]


def test_backfill_skips_pairs_already_in_inbox(env):
    alice, _, _, _, p1_eps, _ = _seed_backfill_world(env)
    # Alice already has the newest p1 episode (e.g. from follow_new fan-out).
    env.repo.insert_many([_entry(alice, p1_eps[2], source="follow_new")])

    # 5 candidates minus the pre-existing pair.
    assert env.repo.backfill_existing_followers(2, dry_run=True) == 4
    assert env.repo.backfill_existing_followers(2) == 4
    # Pre-existing row keeps its provenance.
    assert env.repo.get(alice, p1_eps[2]).source == "follow_new"


def test_backfill_limit_zero_returns_zero(env):
    _seed_backfill_world(env)
    assert env.repo.backfill_existing_followers(0) == 0
    assert env.repo.backfill_existing_followers(-1, dry_run=True) == 0
