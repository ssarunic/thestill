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

"""Unit tests for ``SqliteBriefingRepository`` (spec #36)."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.core.queue_manager import QueueManager
from thestill.models.briefing import Briefing
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "briefing_repo.db"
    SqlitePodcastRepository(str(path))
    QueueManager(str(path))
    return str(path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def repo(db_path):
    return SqliteBriefingRepository(db_path)


def _make_user(user_repo, email: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def _make_briefing(
    user_id: str,
    *,
    cursor_from: datetime,
    cursor_to: datetime,
    episode_count: int = 3,
    created_at: datetime | None = None,
    listened_at: datetime | None = None,
) -> Briefing:
    return Briefing(
        user_id=user_id,
        cursor_from=cursor_from,
        cursor_to=cursor_to,
        episode_count=episode_count,
        created_at=created_at or datetime.now(timezone.utc),
        listened_at=listened_at,
    )


def test_insert_round_trips_briefing(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    briefing = _make_briefing(
        user.id,
        cursor_from=base,
        cursor_to=base + timedelta(hours=6),
        episode_count=4,
    )

    repo.insert(briefing)

    fetched = repo.get(briefing.id)
    assert fetched is not None
    assert fetched.id == briefing.id
    assert fetched.user_id == user.id
    assert fetched.episode_count == 4
    assert fetched.cursor_from == base
    assert fetched.cursor_to == base + timedelta(hours=6)
    assert fetched.script_path is None
    assert fetched.audio_path is None
    assert fetched.listened_at is None


def test_get_returns_none_for_unknown_id(repo):
    assert repo.get("00000000-0000-0000-0000-000000000000") is None


def test_latest_for_user_returns_most_recently_created(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    older = _make_briefing(
        user.id,
        cursor_from=base,
        cursor_to=base + timedelta(hours=1),
        created_at=base + timedelta(hours=1),
    )
    newer = _make_briefing(
        user.id,
        cursor_from=base + timedelta(hours=1),
        cursor_to=base + timedelta(hours=2),
        created_at=base + timedelta(hours=2),
    )
    repo.insert(older)
    repo.insert(newer)

    latest = repo.latest_for_user(user.id)
    assert latest is not None
    assert latest.id == newer.id


def test_latest_for_user_isolates_users(repo, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    repo.insert(
        _make_briefing(
            alice.id,
            cursor_from=base,
            cursor_to=base + timedelta(hours=1),
            created_at=base + timedelta(hours=1),
        )
    )
    bob_briefing = _make_briefing(
        bob.id,
        cursor_from=base,
        cursor_to=base + timedelta(hours=1),
        created_at=base + timedelta(hours=1),
    )
    repo.insert(bob_briefing)

    latest = repo.latest_for_user(bob.id)
    assert latest is not None
    assert latest.id == bob_briefing.id


def test_latest_for_user_returns_none_when_no_briefings(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert repo.latest_for_user(user.id) is None


def test_update_listened_at_persists(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    briefing = _make_briefing(
        user.id,
        cursor_from=base,
        cursor_to=base + timedelta(hours=1),
    )
    repo.insert(briefing)

    listened_at = base + timedelta(hours=2)
    updated = repo.update_listened_at(briefing.id, listened_at)
    assert updated is not None
    assert updated.listened_at == listened_at

    refetched = repo.get(briefing.id)
    assert refetched is not None
    assert refetched.listened_at == listened_at


def test_update_listened_at_returns_none_when_briefing_missing(repo):
    result = repo.update_listened_at("00000000-0000-0000-0000-000000000000", datetime.now(timezone.utc))
    assert result is None


def test_check_constraint_rejects_inverted_cursor(repo, user_repo, db_path):
    """``cursor_to > cursor_from`` is enforced by CHECK at the schema layer."""
    user = _make_user(user_repo, "alice@example.com")
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO user_briefings
                    (id, user_id, cursor_from, cursor_to, episode_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    user.id,
                    (base + timedelta(hours=1)).isoformat(),
                    base.isoformat(),
                    1,
                    base.isoformat(),
                ),
            )
            conn.commit()


def test_list_for_user_returns_newest_first_paginated(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    base = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    for day in range(5):
        repo.insert(
            _make_briefing(
                user.id,
                cursor_from=base + timedelta(days=day - 1),
                cursor_to=base + timedelta(days=day),
                created_at=base + timedelta(days=day),
            )
        )

    first_page = repo.list_for_user(user.id, limit=2, offset=0)
    second_page = repo.list_for_user(user.id, limit=2, offset=2)

    assert [b.created_at for b in first_page] == [
        base + timedelta(days=4),
        base + timedelta(days=3),
    ]
    assert [b.created_at for b in second_page] == [
        base + timedelta(days=2),
        base + timedelta(days=1),
    ]
    assert repo.count_for_user(user.id) == 5


def test_list_for_user_isolates_users(repo, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    base = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
    repo.insert(_make_briefing(alice.id, cursor_from=base, cursor_to=base + timedelta(hours=1)))
    repo.insert(_make_briefing(bob.id, cursor_from=base, cursor_to=base + timedelta(hours=1)))

    listed = repo.list_for_user(alice.id, limit=10, offset=0)

    assert [b.user_id for b in listed] == [alice.id]
    assert repo.count_for_user(alice.id) == 1


def test_count_pending_for_user_uses_real_wait_set_rows(repo, user_repo, db_path):
    """Spec #55 FM-5 matrix: exercise actual follower/inbox/task rows."""
    user = _make_user(user_repo, "alice@example.com")
    other = _make_user(user_repo, "bob@example.com")
    since = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 10, 8, 5, tzinfo=timezone.utc)
    followed_podcast = str(uuid.uuid4())
    unfollowed_podcast = str(uuid.uuid4())

    with sqlite3.connect(db_path) as conn:
        for podcast_id, slug in ((followed_podcast, "followed"), (unfollowed_podcast, "unfollowed")):
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (podcast_id, f"https://example.com/{slug}.xml", slug, slug),
            )
        conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user.id, followed_podcast, since.isoformat()),
        )
        conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), other.id, unfollowed_podcast, since.isoformat()),
        )

        def add_case(name, *, status, pub_date, podcast_id=followed_podcast, retry=0, maximum=3, retry_at=None):
            episode_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO episodes
                    (id, podcast_id, external_id, title, slug, description, description_html, audio_url, pub_date)
                VALUES (?, ?, ?, ?, ?, '', '', ?, ?)
                """,
                (
                    episode_id,
                    podcast_id,
                    f"ext-{name}",
                    name,
                    name,
                    f"https://example.com/{name}.mp3",
                    pub_date.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO tasks
                    (id, episode_id, stage, status, retry_count, max_retries,
                     next_retry_at, created_at, updated_at)
                VALUES (?, ?, 'summarize', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    episode_id,
                    status,
                    retry,
                    maximum,
                    retry_at.isoformat() if retry_at else None,
                    since.isoformat(),
                    since.isoformat(),
                ),
            )
            return episode_id

        in_window = since + timedelta(minutes=30)
        pending = add_case("pending", status="pending", pub_date=in_window)
        # Multiple active rows for one episode still count as one wait-set item.
        conn.execute(
            """
            INSERT INTO tasks
                (id, episode_id, stage, status, retry_count, max_retries, created_at, updated_at)
            VALUES (?, ?, 'clean', 'pending', 0, 3, ?, ?)
            """,
            (str(uuid.uuid4()), pending, since.isoformat(), since.isoformat()),
        )
        add_case("processing", status="processing", pub_date=in_window)
        add_case(
            "retrying",
            status="retry_scheduled",
            pub_date=in_window,
            retry=1,
            maximum=3,
            retry_at=now + timedelta(minutes=5),
        )
        add_case(
            "failed-terminal",
            status="failed",
            pub_date=in_window,
            retry=1,
            maximum=3,
            retry_at=now + timedelta(minutes=5),
        )
        add_case("exhausted", status="failed", pub_date=in_window, retry=3, maximum=3)
        add_case("unfollowed", status="pending", pub_date=in_window, podcast_id=unfollowed_podcast)
        add_case("post-cutoff", status="pending", pub_date=cutoff + timedelta(minutes=1))
        add_case("pre-window", status="pending", pub_date=since - timedelta(minutes=1))
        delivered = add_case("already-delivered", status="pending", pub_date=in_window)
        conn.execute(
            """
            INSERT INTO user_episode_inbox
                (id, user_id, episode_id, source, state, delivered_at)
            VALUES (?, ?, ?, 'follow_new', 'unread', ?)
            """,
            (str(uuid.uuid4()), user.id, delivered, now.isoformat()),
        )
        conn.commit()

    assert (
        repo.count_pending_for_user(
            user.id,
            since=since,
            cutoff=cutoff,
        )
        == 3
    )


def test_list_for_user_empty(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert repo.list_for_user(user.id, limit=10, offset=0) == []
    assert repo.count_for_user(user.id) == 0
