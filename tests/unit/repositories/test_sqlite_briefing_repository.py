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

from thestill.models.briefing import Briefing
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "briefing_repo.db"
    SqlitePodcastRepository(str(path))
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


def test_list_for_user_empty(repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert repo.list_for_user(user.id, limit=10, offset=0) == []
    assert repo.count_for_user(user.id) == 0
