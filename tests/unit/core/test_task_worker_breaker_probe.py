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

"""Half-open probe release on non-infra outcomes (spec #49 L1 follow-up).

2026-07-24 incident: during a Dalston update the transcribe breaker opened on
``runtime_unavailable`` failures, then the half-open probe task died with a
*fatal* server-side schema error. The fatal path never told the breaker, so
``probe_in_flight`` stayed set and ``allow_dispatch`` returned False forever —
the transcribe stage dispatched nothing until a process restart.

These tests pin the rule: any definitive non-infra outcome of a probe
(success, fatal, item-class transient — even a success whose claim lease was
lost) must release the probe slot and close the breaker, while an infra
failure during the probe must still re-open it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from thestill.core.circuit_breaker import CircuitState
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.utils.exceptions import FatalError, TransientError

STAGE = TaskStage.TRANSCRIBE
EPISODE_ID = "00000000-0000-0000-0000-0000000000e1"


def _make_worker(handler, **breaker_kwargs) -> TaskWorker:
    worker = TaskWorker(
        queue_manager=MagicMock(),
        task_handlers={STAGE: handler},
        repository=MagicMock(),
        circuit_breaker_enabled=True,
        circuit_failure_threshold=breaker_kwargs.pop("failure_threshold", 1),
        circuit_cooldown_seconds=breaker_kwargs.pop("cooldown_seconds", 0.0),
        **breaker_kwargs,
    )
    return worker


def _make_task() -> Task:
    return Task(
        id="task-uuid",
        episode_id=EPISODE_ID,
        stage=STAGE,
        status=TaskStatus.PROCESSING,
        started_at=datetime(2026, 7, 24, 9, 45, tzinfo=timezone.utc),
        metadata={"run_full_pipeline": True},
    )


def _trip_to_half_open_probe(worker: TaskWorker) -> None:
    """Drive the breaker to HALF_OPEN with the probe slot reserved — the
    exact state the wedged transcribe stage was stuck in."""
    breaker = worker._breaker
    breaker.record_failure(STAGE.value)  # threshold=1 → OPEN
    assert breaker.state(STAGE.value) == CircuitState.OPEN
    assert breaker.allow_dispatch(STAGE.value) is True  # cooldown=0 → probe out
    assert breaker.state(STAGE.value) == CircuitState.HALF_OPEN
    # Wedge precondition: no second dispatch while the probe is in flight.
    assert breaker.allow_dispatch(STAGE.value) is False


class TestProbeReleaseOnNonInfraOutcome:
    def test_fatal_probe_closes_breaker(self):
        """The incident regression: a probe that dies fatally (DLQ) must not
        leave the stage wedged — the dependency answered, the item was bad."""
        handler = MagicMock(side_effect=FatalError("Job failed: Transcript assembly failed"))
        worker = _make_worker(handler)
        _trip_to_half_open_probe(worker)

        worker._process_task(_make_task())

        assert worker._breaker.state(STAGE.value) == CircuitState.CLOSED
        assert worker._breaker.allow_dispatch(STAGE.value) is True
        worker.queue_manager.mark_dead.assert_called_once()

    def test_item_transient_probe_closes_breaker(self):
        """An item-class transient during the probe is equally definitive:
        the dependency responded, so the probe must be released."""
        handler = MagicMock(side_effect=TransientError("bad item, will retry"))
        worker = _make_worker(handler)
        _trip_to_half_open_probe(worker)

        worker._process_task(_make_task())

        assert worker._breaker.state(STAGE.value) == CircuitState.CLOSED
        assert worker._breaker.allow_dispatch(STAGE.value) is True

    def test_success_with_lost_claim_closes_breaker(self):
        """A probe whose handler succeeds but whose claim lease was lost still
        proves the dependency recovered — the early return must not skip the
        breaker bookkeeping."""
        handler = MagicMock()  # succeeds
        worker = _make_worker(handler)
        worker.queue_manager.complete_task.return_value = False  # claim lost
        _trip_to_half_open_probe(worker)

        worker._process_task(_make_task())

        assert worker._breaker.state(STAGE.value) == CircuitState.CLOSED
        assert worker._breaker.allow_dispatch(STAGE.value) is True
        # The lease guard still held: no successor fan-out happened.
        worker.queue_manager.supersede_stale_tasks.assert_not_called()
        worker.queue_manager.add_task.assert_not_called()


class TestExistingBreakerSemanticsPreserved:
    def test_infra_probe_failure_still_reopens(self):
        """An infra failure during the probe means the dependency is still
        down — the breaker must re-open, not close."""
        err = TransientError("dependency down")
        err.error_class = "infra"
        handler = MagicMock(side_effect=err)
        worker = _make_worker(handler)
        _trip_to_half_open_probe(worker)

        worker._process_task(_make_task())

        assert worker._breaker.state(STAGE.value) == CircuitState.OPEN

    def test_fatal_on_closed_breaker_does_not_clear_failure_window(self):
        """Item/fatal failures stay invisible to a CLOSED breaker: two infra
        failures + a fatal task + a third infra failure must still trip it."""
        handler = MagicMock(side_effect=FatalError("corrupt input"))
        worker = _make_worker(handler, failure_threshold=3)
        breaker = worker._breaker
        breaker.record_failure(STAGE.value)
        breaker.record_failure(STAGE.value)
        assert breaker.state(STAGE.value) == CircuitState.CLOSED

        worker._process_task(_make_task())
        assert breaker.state(STAGE.value) == CircuitState.CLOSED

        # Window intact → the third infra failure trips OPEN.
        assert breaker.record_failure(STAGE.value) == CircuitState.OPEN
