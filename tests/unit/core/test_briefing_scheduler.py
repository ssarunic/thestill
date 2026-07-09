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

Spec #51 (``TestEmailDelivery``): the slot-fire → ensure_pending →
delivery-pass chain, including the lazy-then-scheduled throttle case that
must still email exactly once.
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
    email_enabled: bool = False,
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
            email_enabled=email_enabled,
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


class TestNarrationChaining:
    """Phase 4 (#33 interlock): scheduled runs chain narration."""

    @pytest.fixture
    def narration_runner(self):
        runner = MagicMock()
        runner.artifact_exists.return_value = False
        run = MagicMock()
        run.narration_id = "b1-medium"
        run.content.mode = "narrated"
        runner.run.return_value = run
        return runner

    @pytest.fixture
    def scheduler(self, schedule_repo, briefing_service, narration_runner):
        return BriefingScheduler(
            schedule_repo,
            briefing_service,
            tick_seconds=60,
            max_per_tick=50,
            narration_runner=narration_runner,
            narration_target_seconds=300,
        )

    def test_successful_generation_chains_narration(
        self, scheduler, user_repo, schedule_repo, briefing_service, narration_runner
    ):
        _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)

        assert scheduler.tick(now=SLOT) == 1

        narration_runner.run.assert_called_once()
        kwargs = narration_runner.run.call_args.kwargs
        assert kwargs["briefing_id"]
        assert kwargs["target_duration_seconds"] == 300
        assert kwargs["slug"] == "medium"  # 300s preset

    def test_empty_window_does_not_narrate(
        self, scheduler, user_repo, schedule_repo, briefing_service, narration_runner
    ):
        _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: None

        assert scheduler.tick(now=SLOT) == 0
        narration_runner.run.assert_not_called()

    def test_existing_artifact_skips_renarration(self, scheduler, user_repo, schedule_repo, narration_runner):
        # Throttle-returned briefing already narrated (lazy open at 7:30 +
        # manual narrate) — the scheduled slot must not re-spend the LLM.
        _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)
        narration_runner.artifact_exists.return_value = True

        assert scheduler.tick(now=SLOT) == 1
        narration_runner.run.assert_not_called()

    def test_narration_failure_is_isolated(
        self, scheduler, user_repo, schedule_repo, briefing_service, narration_runner
    ):
        # A narration blow-up must not fail the run (the script exists) nor
        # block the next due user's generation + narration.
        first = _add_user_with_schedule(user_repo, schedule_repo, email="a@example.com", next_run_at=SLOT)
        second = _add_user_with_schedule(
            user_repo, schedule_repo, email="b@example.com", next_run_at=SLOT + timedelta(minutes=1)
        )
        narration_runner.run.side_effect = [RuntimeError("LLM provider down"), MagicMock()]

        now = SLOT + timedelta(minutes=2)
        assert scheduler.tick(now=now) == 2
        assert narration_runner.run.call_count == 2
        called_users = [call.args[0] for call in briefing_service.generate_for_user.call_args_list]
        assert called_users == [first.id, second.id]
        assert schedule_repo.get(first.id).next_run_at > now
        assert schedule_repo.get(second.id).next_run_at > now

    def test_no_runner_means_no_chaining(self, schedule_repo, briefing_service, user_repo):
        # Narration disabled (NARRATION_ENABLED=false) — generation-only
        # scheduling keeps working exactly as before Phase 4.
        _add_user_with_schedule(user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT)
        scheduler = BriefingScheduler(schedule_repo, briefing_service, tick_seconds=60, max_per_tick=50)

        assert scheduler.tick(now=SLOT) == 1


class TestEmailDelivery:
    """Spec #51: slot fire → ensure_pending → delivery pass, exactly once."""

    @pytest.fixture
    def delivery_stack(self, db_path, tmp_path):
        """Real delivery service over the shared SQLite db + a fake sender."""
        from thestill.repositories.sqlite_briefing_delivery_repository import SqliteBriefingDeliveryRepository
        from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
        from thestill.services.briefing_delivery_service import BriefingDeliveryService
        from thestill.services.briefing_email_renderer import BriefingEmailRenderer
        from thestill.services.email_sender import EmailSender

        class FakeSender(EmailSender):
            def __init__(self):
                self.sent = []

            def send(self, *, to, subject, html, text, headers=None):
                self.sent.append(to)

        sender = FakeSender()
        briefing_repo = SqliteBriefingRepository(db_path)
        delivery_repo = SqliteBriefingDeliveryRepository(db_path)

        def persist_briefing(user_id: str) -> Briefing:
            script = tmp_path / f"script-{uuid.uuid4().hex}.md"
            script.write_text("# Morning Briefing\n\nHello.\n", encoding="utf-8")
            briefing = _briefing(user_id)
            briefing.script_path = str(script)
            briefing_repo.insert(briefing)
            return briefing

        def make_service(schedule_repo, user_repo):
            from thestill.utils.file_storage import LocalFileStorage
            from thestill.utils.path_manager import PathManager

            return BriefingDeliveryService(
                delivery_repo,
                briefing_repo,
                schedule_repo,
                user_repo,
                BriefingEmailRenderer(public_base_url="https://app.example.com", secret="test-secret"),
                sender,
                path_manager=PathManager(str(tmp_path)),
                file_storage=LocalFileStorage(str(tmp_path)),
            )

        return {"sender": sender, "persist": persist_briefing, "make_service": make_service}

    @pytest.fixture
    def email_scheduler(self, schedule_repo, user_repo, briefing_service, delivery_stack):
        return BriefingScheduler(
            schedule_repo,
            briefing_service,
            tick_seconds=60,
            max_per_tick=50,
            delivery_service=delivery_stack["make_service"](schedule_repo, user_repo),
        )

    def test_slot_fire_emails_exactly_once(
        self, email_scheduler, user_repo, schedule_repo, briefing_service, delivery_stack
    ):
        user = _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=True
        )
        briefing = delivery_stack["persist"](user.id)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: briefing

        assert email_scheduler.tick(now=SLOT) == 1
        assert delivery_stack["sender"].sent == ["alice@example.com"]
        # Subsequent ticks (retry pass runs every tick) never re-send.
        email_scheduler.tick(now=SLOT + timedelta(minutes=1))
        assert delivery_stack["sender"].sent == ["alice@example.com"]

    def test_lazy_then_scheduled_still_emails_exactly_once(
        self, email_scheduler, user_repo, schedule_repo, briefing_service, delivery_stack
    ):
        # 7:30 lazy open generated the briefing; the 8:00 slot gets the
        # throttle-returned existing one. The delivery is keyed on "this
        # briefing hasn't been emailed yet", so it still goes out — once.
        user = _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=True
        )
        existing = delivery_stack["persist"](user.id)  # the 7:30 lazy briefing
        briefing_service.generate_for_user.side_effect = lambda *a, **k: existing

        assert email_scheduler.tick(now=SLOT) == 1
        # A later manual tick touching the same briefing again (e.g. next
        # slot throttled to the same row) adds no second email.
        schedule_repo.upsert(
            BriefingSchedule(
                user_id=user.id,
                timezone_name="Europe/Zagreb",
                email_enabled=True,
                next_run_at=SLOT + timedelta(hours=1),
            )
        )
        email_scheduler.tick(now=SLOT + timedelta(hours=1))
        assert delivery_stack["sender"].sent == ["alice@example.com"]

    def test_email_disabled_schedule_sends_nothing(
        self, email_scheduler, user_repo, schedule_repo, briefing_service, delivery_stack
    ):
        user = _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=False
        )
        briefing = delivery_stack["persist"](user.id)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: briefing

        assert email_scheduler.tick(now=SLOT) == 1  # generation still happens
        assert delivery_stack["sender"].sent == []

    def test_empty_window_produces_no_delivery(
        self, email_scheduler, user_repo, schedule_repo, briefing_service, delivery_stack
    ):
        _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=True
        )
        briefing_service.generate_for_user.side_effect = lambda *a, **k: None

        assert email_scheduler.tick(now=SLOT) == 0
        assert delivery_stack["sender"].sent == []

    def test_delivery_pass_failure_never_fails_the_tick(
        self, schedule_repo, user_repo, briefing_service, delivery_stack
    ):
        user = _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=True
        )
        briefing = delivery_stack["persist"](user.id)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: briefing
        exploding = MagicMock()
        exploding.ensure_pending.side_effect = RuntimeError("db hiccup")
        exploding.deliver_due.side_effect = RuntimeError("db hiccup")
        scheduler = BriefingScheduler(
            schedule_repo,
            briefing_service,
            tick_seconds=60,
            max_per_tick=50,
            delivery_service=exploding,
        )

        assert scheduler.tick(now=SLOT) == 1  # generation unaffected
        assert schedule_repo.get(user.id).next_run_at > SLOT

    def test_transient_queue_failure_recovers_next_tick(
        self, user_repo, schedule_repo, briefing_service, delivery_stack
    ):
        # The slot has already advanced when ensure_pending blows up, so
        # without a retry the scheduled email would be lost until the next
        # cadence. The scheduler buffers the briefing id and re-queues it
        # (idempotently) on the following tick.
        user = _add_user_with_schedule(
            user_repo, schedule_repo, email="alice@example.com", next_run_at=SLOT, email_enabled=True
        )
        briefing = delivery_stack["persist"](user.id)
        briefing_service.generate_for_user.side_effect = lambda *a, **k: briefing
        flaky = MagicMock()
        flaky.ensure_pending.side_effect = [RuntimeError("db hiccup"), True]
        scheduler = BriefingScheduler(
            schedule_repo,
            briefing_service,
            tick_seconds=60,
            max_per_tick=50,
            delivery_service=flaky,
        )

        assert scheduler.tick(now=SLOT) == 1  # queueing failed, swallowed
        scheduler.tick(now=SLOT + timedelta(minutes=1))  # no due slot: retry phase only

        assert flaky.ensure_pending.call_count == 2
        assert flaky.ensure_pending.call_args.args[0] == briefing.id
        # Recovered: later ticks don't re-queue.
        scheduler.tick(now=SLOT + timedelta(minutes=2))
        assert flaky.ensure_pending.call_count == 2


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
