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

"""Unit tests for ``BriefingScheduler`` (spec #50).

Tick semantics against a real SQLite schedule repository and a mocked
``BriefingService``: due-selection, claim-before-generate, per-user
failure isolation (FM-1), and single-fire downtime catch-up.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from thestill.core.briefing_scheduler import BriefingScheduler
from thestill.models.briefing import Briefing
from thestill.models.briefing_schedule import BriefingFrequency, BriefingSchedule
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_schedule_repository import SqliteBriefingScheduleRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

# Tuesday 08:00 CEST = 06:00 UTC — a daily-8am Zagreb slot.
SLOT = datetime(2026, 7, 7, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "scheduler.db"
    SqlitePodcastRepository(str(path))
    return str(path)


@pytest.fixture
def schedule_repo(db_path):
    return SqliteBriefingScheduleRepository(db_path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def briefing_service():
    service = MagicMock()
    service.generate_for_user.side_effect = lambda user_id, **kwargs: _briefing(user_id)
    return service


@pytest.fixture
def scheduler(schedule_repo, briefing_service):
    return BriefingScheduler(schedule_repo, briefing_service, tick_seconds=60, max_per_tick=50)


def _briefing(user_id: str) -> Briefing:
    return Briefing(
        user_id=user_id,
        cursor_from=SLOT - timedelta(days=1),
        cursor_to=SLOT,
        episode_count=3,
        created_at=SLOT,
    )


def _add_user_with_schedule(
    user_repo,
    schedule_repo,
    *,
    email: str,
    next_run_at: datetime | None,
    enabled: bool = True,
    hour_local: int = 8,
) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    schedule_repo.upsert(
        BriefingSchedule(
            user_id=user.id,
            frequency=BriefingFrequency.DAILY,
            hour_local=hour_local,
            timezone_name="Europe/Zagreb",
            enabled=enabled,
            next_run_at=next_run_at,
        )
    )
    return user


class TestTick:
    def test_generates_for_due_users_only(self, scheduler, user_repo, schedule_repo, briefing_service):
        due = _add_user_with_schedule(user_repo, schedule_repo, email="due@example.com", next_run_at=SLOT)
        _add_user_with_schedule(
            user_repo,
            schedule_repo,
            email="later@example.com",
            next_run_at=SLOT + timedelta(hours=3),
            hour_local=11,
        )

        generated = scheduler.tick(now=SLOT)

        assert generated == 1
        briefing_service.generate_for_user.assert_called_once_with(due.id, now=SLOT)

    def test_each_user_fires_at_their_own_hour(self, scheduler, user_repo, schedule_repo, briefing_service):
        early = _add_user_with_schedule(
            user_repo, schedule_repo, email="six@example.com", next_run_at=SLOT, hour_local=8
        )
        late = _add_user_with_schedule(
            user_repo,
            schedule_repo,
            email="eleven@example.com",
            next_run_at=SLOT + timedelta(hours=3),
            hour_local=11,
        )

        assert scheduler.tick(now=SLOT) == 1
        assert scheduler.tick(now=SLOT + timedelta(hours=3)) == 1
        called_users = [call.args[0] for call in briefing_service.generate_for_user.call_args_list]
        assert called_users == [early.id, late.id]

    def test_slot_advances_to_next_future_occurrence(self, scheduler, user_repo, schedule_repo):
        user = _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)

        scheduler.tick(now=SLOT)

        next_run = schedule_repo.get(user.id).next_run_at
        assert next_run == SLOT + timedelta(days=1)  # 08:00 CEST tomorrow

    def test_downtime_catchup_fires_once(self, scheduler, user_repo, schedule_repo, briefing_service):
        # Server was down for three days: the slot is 3 days stale. One
        # generation covers the widened window; next_run_at jumps straight
        # to the next *future* occurrence, never replaying missed slots.
        user = _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)
        restart = SLOT + timedelta(days=3, hours=5, minutes=30)  # Fri 11:30 UTC

        assert scheduler.tick(now=restart) == 1
        assert scheduler.tick(now=restart + timedelta(minutes=1)) == 0
        briefing_service.generate_for_user.assert_called_once()
        assert schedule_repo.get(user.id).next_run_at == SLOT + timedelta(days=4)  # Sat 08:00 CEST

    def test_empty_window_advances_slot_without_counting(self, scheduler, user_repo, schedule_repo, briefing_service):
        user = _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: None

        assert scheduler.tick(now=SLOT) == 0
        assert schedule_repo.get(user.id).next_run_at == SLOT + timedelta(days=1)

    def test_failed_generation_is_isolated_and_slot_still_advances(
        self, scheduler, user_repo, schedule_repo, briefing_service
    ):
        # FM-1: the failing user is logged and skipped; the healthy user
        # still gets their briefing; the failed slot advances so the error
        # surfaces once per cadence, not once per tick.
        failing = _add_user_with_schedule(user_repo, schedule_repo, email="fail@example.com", next_run_at=SLOT)
        healthy = _add_user_with_schedule(
            user_repo, schedule_repo, email="ok@example.com", next_run_at=SLOT + timedelta(minutes=1)
        )

        def generate(user_id, **kwargs):
            if user_id == failing.id:
                raise RuntimeError("LLM provider down")
            return _briefing(user_id)

        briefing_service.generate_for_user.side_effect = generate

        now = SLOT + timedelta(minutes=2)
        assert scheduler.tick(now=now) == 1
        assert schedule_repo.get(failing.id).next_run_at > now
        assert schedule_repo.get(healthy.id).next_run_at > now

    def test_lost_claim_skips_generation(self, schedule_repo, briefing_service, user_repo):
        # Simulate a second instance winning the claim between due() and
        # claim(): the repo says the slot moved, so no generation happens.
        user = _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)

        losing_repo = MagicMock(wraps=schedule_repo)
        losing_repo.claim.return_value = False
        scheduler = BriefingScheduler(losing_repo, briefing_service, tick_seconds=60, max_per_tick=50)

        assert scheduler.tick(now=SLOT) == 0
        briefing_service.generate_for_user.assert_not_called()
        # The real row is untouched — the "other instance" owns it.
        assert schedule_repo.get(user.id).next_run_at == SLOT

    def test_respects_max_per_tick(self, schedule_repo, briefing_service, user_repo):
        for i in range(3):
            _add_user_with_schedule(user_repo, schedule_repo, email=f"u{i}@example.com", next_run_at=SLOT)
        scheduler = BriefingScheduler(schedule_repo, briefing_service, tick_seconds=60, max_per_tick=2)

        assert scheduler.tick(now=SLOT) == 2
        # The third user is picked up by the next tick, not dropped.
        assert scheduler.tick(now=SLOT) == 1


class TestLifecycle:
    def test_start_stop(self, scheduler):
        scheduler.start()
        assert scheduler.is_running()
        scheduler.stop()
        assert not scheduler.is_running()

    def test_double_start_is_noop(self, scheduler):
        scheduler.start()
        thread = scheduler._thread
        scheduler.start()
        assert scheduler._thread is thread
        scheduler.stop()
