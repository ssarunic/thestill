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

"""Spec #49 Layer 1 — per-stage circuit breaker.

Two layers of coverage:

- ``StageCircuitBreaker`` state machine with an injected clock — closed→open on
  threshold, window expiry, open→half-open after cooldown, single-probe
  half-open, probe success closes / probe failure re-opens, and the
  cancel-dispatch (empty-queue) probe release.
- ``TaskWorker._handle_transient_failure`` — an infra failure that trips the
  breaker is parked WITHOUT spending the task's retry budget.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from thestill.core.circuit_breaker import CircuitState, StageCircuitBreaker
from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

STAGE = "transcribe"
EPISODE_ID = "11111111-1111-1111-1111-111111111111"


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def breaker(clock: FakeClock) -> StageCircuitBreaker:
    return StageCircuitBreaker(
        failure_threshold=3,
        window_seconds=120.0,
        cooldown_seconds=60.0,
        clock=clock,
    )


class TestStateMachine:
    def test_starts_closed_and_dispatches(self, breaker):
        assert breaker.state(STAGE) == CircuitState.CLOSED
        assert breaker.allow_dispatch(STAGE) is True
        assert breaker.is_tripped(STAGE) is False

    def test_below_threshold_stays_closed(self, breaker):
        breaker.record_failure(STAGE)
        breaker.record_failure(STAGE)
        assert breaker.state(STAGE) == CircuitState.CLOSED
        assert breaker.allow_dispatch(STAGE) is True

    def test_threshold_opens_and_blocks_dispatch(self, breaker):
        for _ in range(3):
            breaker.record_failure(STAGE)
        assert breaker.state(STAGE) == CircuitState.OPEN
        assert breaker.is_tripped(STAGE) is True
        assert breaker.allow_dispatch(STAGE) is False

    def test_failures_outside_window_dont_count(self, breaker, clock):
        breaker.record_failure(STAGE)
        breaker.record_failure(STAGE)
        clock.advance(121.0)  # both fall out of the 120s window
        breaker.record_failure(STAGE)
        assert breaker.state(STAGE) == CircuitState.CLOSED

    def test_open_promotes_to_half_open_after_cooldown(self, breaker, clock):
        for _ in range(3):
            breaker.record_failure(STAGE)
        assert breaker.allow_dispatch(STAGE) is False
        clock.advance(60.0)
        # First call after cooldown promotes to half-open and reserves the probe.
        assert breaker.allow_dispatch(STAGE) is True
        assert breaker.state(STAGE) == CircuitState.HALF_OPEN
        # Second call is blocked — only one probe at a time.
        assert breaker.allow_dispatch(STAGE) is False

    def test_probe_success_closes(self, breaker, clock):
        for _ in range(3):
            breaker.record_failure(STAGE)
        clock.advance(60.0)
        breaker.allow_dispatch(STAGE)  # probe
        breaker.record_success(STAGE)
        assert breaker.state(STAGE) == CircuitState.CLOSED
        assert breaker.allow_dispatch(STAGE) is True

    def test_probe_failure_reopens(self, breaker, clock):
        for _ in range(3):
            breaker.record_failure(STAGE)
        clock.advance(60.0)
        breaker.allow_dispatch(STAGE)  # probe
        state = breaker.record_failure(STAGE)
        assert state == CircuitState.OPEN
        assert breaker.allow_dispatch(STAGE) is False
        # Re-opens with a fresh cooldown, not immediately probe-able.
        clock.advance(60.0)
        assert breaker.allow_dispatch(STAGE) is True

    def test_cancel_dispatch_releases_probe(self, breaker, clock):
        for _ in range(3):
            breaker.record_failure(STAGE)
        clock.advance(60.0)
        assert breaker.allow_dispatch(STAGE) is True  # reserves probe
        breaker.cancel_dispatch(STAGE)  # empty queue — release it
        assert breaker.state(STAGE) == CircuitState.OPEN
        # Cooldown restarted; not immediately probe-able again.
        assert breaker.allow_dispatch(STAGE) is False
        clock.advance(60.0)
        assert breaker.allow_dispatch(STAGE) is True

    def test_success_clears_failure_window(self, breaker):
        breaker.record_failure(STAGE)
        breaker.record_failure(STAGE)
        breaker.record_success(STAGE)
        breaker.record_failure(STAGE)
        breaker.record_failure(STAGE)
        # Only two failures since the success — still closed.
        assert breaker.state(STAGE) == CircuitState.CLOSED

    def test_stages_are_independent(self, breaker):
        for _ in range(3):
            breaker.record_failure("transcribe")
        assert breaker.state("transcribe") == CircuitState.OPEN
        assert breaker.state("download") == CircuitState.CLOSED

    def test_snapshot_only_lists_non_closed(self, breaker):
        for _ in range(3):
            breaker.record_failure("transcribe")
        breaker.record_failure("download")  # below threshold → closed
        assert breaker.snapshot() == {"transcribe": "open"}


@pytest.fixture
def qm(tmp_path: Path) -> QueueManager:
    db = str(tmp_path / "cb.db")
    repo = SqlitePodcastRepository(db_path=db)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="CB Test",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="CB Episode",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return QueueManager(db)


class TestWorkerBudgetBypass:
    def _worker(self, qm: QueueManager) -> TaskWorker:
        return TaskWorker(
            qm,
            task_handlers={},
            circuit_breaker_enabled=True,
            circuit_failure_threshold=3,
            circuit_window_seconds=120.0,
            circuit_cooldown_seconds=60.0,
        )

    def test_infra_failure_parks_without_spending_budget_once_open(self, qm):
        worker = self._worker(qm)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        msg = "Failed to connect: [Errno 8] nodename nor servname provided"

        # First two failures are below threshold → budget spent normally.
        worker._handle_transient_failure(task, msg, "infra")
        worker._handle_transient_failure(task, msg, "infra")
        assert qm.get_task(task.id).retry_count == 2

        # Third failure trips the breaker → parked without spending budget.
        result = worker._handle_transient_failure(task, msg, "infra")
        assert result.status == TaskStatus.RETRY_SCHEDULED
        assert qm.get_task(task.id).retry_count == 2  # unchanged
        assert worker._breaker.state("transcribe") == CircuitState.OPEN

    def test_item_failure_always_spends_budget(self, qm):
        worker = self._worker(qm)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        # Item-class failures never touch the breaker, even past the threshold.
        for _ in range(5):
            worker._handle_transient_failure(task, "Read timed out", "item")
        # Exhausted the 3-strike budget → failed, breaker untouched.
        assert qm.get_task(task.id).status == TaskStatus.FAILED
        assert worker._breaker.snapshot() == {}
