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

"""Unit tests for ``SqliteBriefingScheduleRepository`` (spec #50)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing_schedule import BriefingFrequency, BriefingSchedule
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_schedule_repository import SqliteBriefingScheduleRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

NOW = datetime(2026, 7, 7, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "briefing_schedules.db"
    SqlitePodcastRepository(str(path))
    return str(path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def repo(db_path):
    return SqliteBriefingScheduleRepository(db_path)


def _make_user(user_repo, email: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def _schedule(user_id: str, *, next_run_at: datetime | None = NOW, **overrides) -> BriefingSchedule:
    defaults = dict(
        user_id=user_id,
        frequency=BriefingFrequency.DAILY,
        hour_local=8,
        weekday=None,
        timezone_name="Europe/Zagreb",
        enabled=True,
        next_run_at=next_run_at,
    )
    defaults.update(overrides)
    return BriefingSchedule(**defaults)


class TestGetUpsert:
    def test_get_returns_none_when_never_configured(self, repo):
        assert repo.get(str(uuid.uuid4())) is None

    def test_upsert_then_get_roundtrip(self, repo, user_repo):
        user = _make_user(user_repo, "alice@example.com")
        schedule = _schedule(user.id, frequency=BriefingFrequency.WEEKLY, weekday=0, hour_local=7)
        repo.upsert(schedule)

        loaded = repo.get(user.id)
        assert loaded is not None
        assert loaded.frequency == BriefingFrequency.WEEKLY
        assert loaded.weekday == 0
        assert loaded.hour_local == 7
        assert loaded.timezone_name == "Europe/Zagreb"
        assert loaded.enabled is True
        assert loaded.next_run_at == NOW

    def test_upsert_replaces_existing_row(self, repo, user_repo):
        user = _make_user(user_repo, "alice@example.com")
        repo.upsert(_schedule(user.id))
        repo.upsert(_schedule(user.id, hour_local=6, enabled=False, next_run_at=None))

        loaded = repo.get(user.id)
        assert loaded.hour_local == 6
        assert loaded.enabled is False
        assert loaded.next_run_at is None

    def test_one_row_per_user(self, repo, user_repo, db_path):
        user = _make_user(user_repo, "alice@example.com")
        repo.upsert(_schedule(user.id))
        repo.upsert(_schedule(user.id, hour_local=9))

        import sqlite3

        with sqlite3.connect(db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM user_briefing_schedules WHERE user_id = ?", (user.id,)
            ).fetchone()[0]
        assert count == 1


class TestDue:
    def test_due_returns_only_elapsed_enabled_rows(self, repo, user_repo):
        due_user = _make_user(user_repo, "due@example.com")
        future_user = _make_user(user_repo, "future@example.com")
        disabled_user = _make_user(user_repo, "disabled@example.com")
        parked_user = _make_user(user_repo, "parked@example.com")

        repo.upsert(_schedule(due_user.id, next_run_at=NOW - timedelta(minutes=5)))
        repo.upsert(_schedule(future_user.id, next_run_at=NOW + timedelta(hours=1)))
        repo.upsert(_schedule(disabled_user.id, enabled=False, next_run_at=None))
        repo.upsert(_schedule(parked_user.id, next_run_at=None))

        due = repo.due(NOW, limit=10)
        assert [s.user_id for s in due] == [due_user.id]

    def test_due_orders_oldest_first_and_honors_limit(self, repo, user_repo):
        users = [_make_user(user_repo, f"u{i}@example.com") for i in range(3)]
        for i, user in enumerate(users):
            repo.upsert(_schedule(user.id, next_run_at=NOW - timedelta(minutes=30 - i * 10)))

        due = repo.due(NOW, limit=2)
        assert [s.user_id for s in due] == [users[0].id, users[1].id]

    def test_due_boundary_is_inclusive(self, repo, user_repo):
        user = _make_user(user_repo, "edge@example.com")
        repo.upsert(_schedule(user.id, next_run_at=NOW))
        assert [s.user_id for s in repo.due(NOW, limit=10)] == [user.id]


class TestClaim:
    def test_claim_advances_when_expected_matches(self, repo, user_repo):
        user = _make_user(user_repo, "alice@example.com")
        repo.upsert(_schedule(user.id, next_run_at=NOW))
        new_next = NOW + timedelta(days=1)

        assert repo.claim(user.id, expected_next_run_at=NOW, new_next_run_at=new_next) is True
        assert repo.get(user.id).next_run_at == new_next

    def test_claim_fails_when_slot_already_taken(self, repo, user_repo):
        user = _make_user(user_repo, "alice@example.com")
        repo.upsert(_schedule(user.id, next_run_at=NOW))
        new_next = NOW + timedelta(days=1)

        assert repo.claim(user.id, expected_next_run_at=NOW, new_next_run_at=new_next) is True
        # Second claimant read the same due row but the slot moved on.
        assert repo.claim(user.id, expected_next_run_at=NOW, new_next_run_at=new_next) is False
        assert repo.get(user.id).next_run_at == new_next

    def test_claim_fails_when_disabled_mid_tick(self, repo, user_repo):
        user = _make_user(user_repo, "alice@example.com")
        repo.upsert(_schedule(user.id, next_run_at=NOW))
        repo.upsert(_schedule(user.id, enabled=False, next_run_at=None))

        assert repo.claim(user.id, expected_next_run_at=NOW, new_next_run_at=NOW + timedelta(days=1)) is False

    def test_claim_unknown_user_returns_false(self, repo):
        assert repo.claim(str(uuid.uuid4()), expected_next_run_at=NOW, new_next_run_at=NOW + timedelta(days=1)) is False
