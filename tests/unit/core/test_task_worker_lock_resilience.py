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

"""Worker-level resilience for the SQLite-lock wedge.

Pairs with ``test_queue_manager_lock_retry.py``. The queue-manager layer
retries lock errors; this file exercises the worker's *second line of
defence*:

- ``_safe_schedule_retry`` skips when the task already landed in
  ``completed`` (post-handler bookkeeping was the failure, not the work).
- ``_safe_schedule_retry`` and ``_safe_mark_dead`` swallow secondary
  failures so a row never stays in ``processing``.
- ``_periodic_stale_task_reset`` actually runs ``_reset_stale_tasks``
  repeatedly inside the worker's async loop — the watchdog that finally
  reclaims a row if every other layer failed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker


def _make_task(stage: TaskStage = TaskStage.CLEAN, status: TaskStatus = TaskStatus.PROCESSING) -> Task:
    return Task(id="task-uuid", episode_id="episode-uuid", stage=stage, status=status)


def _make_worker(queue: MagicMock) -> TaskWorker:
    return TaskWorker(queue_manager=queue, task_handlers={}, repository=None)


class TestSafeScheduleRetry:
    def test_skips_reschedule_when_task_already_completed(self):
        """Handler succeeded, bookkeeping failed — don't re-run the stage."""
        queue = MagicMock()
        queue.get_task.return_value = _make_task(status=TaskStatus.COMPLETED)
        worker = _make_worker(queue)

        result = worker._safe_schedule_retry(_make_task(), "post-handler bookkeeping failed")

        assert result is not None and result.status == TaskStatus.COMPLETED
        queue.schedule_retry.assert_not_called()

    def test_reschedules_when_task_still_processing(self):
        """Real handler failure — reschedule as before."""
        queue = MagicMock()
        queue.get_task.return_value = _make_task(status=TaskStatus.PROCESSING)
        rescheduled = _make_task(status=TaskStatus.RETRY_SCHEDULED)
        queue.schedule_retry.return_value = rescheduled
        worker = _make_worker(queue)

        result = worker._safe_schedule_retry(_make_task(), "real failure")

        assert result is rescheduled
        # error_class defaults to None when the caller doesn't classify (spec #49).
        queue.schedule_retry.assert_called_once_with("task-uuid", "real failure", None)

    def test_swallows_schedule_retry_failure(self):
        """A secondary lock on ``schedule_retry`` must not propagate.

        The periodic stale-task reset is the safety net that recovers the
        row; raising here would crash the worker poll task.
        """
        queue = MagicMock()
        queue.get_task.return_value = _make_task(status=TaskStatus.PROCESSING)
        queue.schedule_retry.side_effect = Exception("database is locked (still)")
        worker = _make_worker(queue)

        # Must not raise.
        result = worker._safe_schedule_retry(_make_task(), "original error")
        assert result is None

    def test_swallows_get_task_failure_and_still_attempts_reschedule(self):
        """Pre-flight ``get_task`` lock failure shouldn't prevent the retry attempt."""
        queue = MagicMock()
        queue.get_task.side_effect = Exception("database is locked (read)")
        rescheduled = _make_task(status=TaskStatus.RETRY_SCHEDULED)
        queue.schedule_retry.return_value = rescheduled
        worker = _make_worker(queue)

        result = worker._safe_schedule_retry(_make_task(), "original")

        assert result is rescheduled
        queue.schedule_retry.assert_called_once()


class TestSafeMarkDead:
    def test_swallows_mark_dead_failure(self):
        """A secondary lock on ``mark_dead`` must not propagate."""
        queue = MagicMock()
        queue.mark_dead.side_effect = Exception("database is locked")
        worker = _make_worker(queue)

        # Must not raise.
        worker._safe_mark_dead(_make_task(), "fatal boom")
        queue.mark_dead.assert_called_once()


class TestPeriodicStaleReset:
    def test_periodic_reset_calls_reset_until_stopped(self, monkeypatch):
        """The watchdog must actually re-run ``_reset_stale_tasks``.

        Without this, a wedged task waits for the next server restart.
        """
        queue = MagicMock()
        queue.reset_stale_tasks.return_value = 1
        worker = TaskWorker(
            queue_manager=queue,
            task_handlers={},
            stale_timeout_minutes=1,
        )

        sleep_calls = {"n": 0}

        async def fast_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 3:
                worker._running = False

        monkeypatch.setattr(worker, "_sleep_unless_stopped", fast_sleep)

        worker._running = True
        asyncio.run(worker._periodic_stale_task_reset())

        # Two iterations call reset (sleep → check running → reset). The
        # third iteration flips ``_running`` mid-sleep and exits without
        # calling reset again.
        assert queue.reset_stale_tasks.call_count >= 2

    def test_periodic_reset_survives_reset_exception(self, monkeypatch):
        """An exception inside ``_reset_stale_tasks`` must not kill the watchdog."""
        queue = MagicMock()
        queue.reset_stale_tasks.side_effect = [Exception("locked"), 0, 0]
        worker = TaskWorker(
            queue_manager=queue,
            task_handlers={},
            stale_timeout_minutes=1,
        )

        sleep_calls = {"n": 0}

        async def fast_sleep(_seconds):
            sleep_calls["n"] += 1
            if sleep_calls["n"] >= 3:
                worker._running = False

        monkeypatch.setattr(worker, "_sleep_unless_stopped", fast_sleep)

        worker._running = True
        asyncio.run(worker._periodic_stale_task_reset())  # must not raise

        assert queue.reset_stale_tasks.call_count >= 2

    def test_sleep_unless_stopped_wakes_promptly_on_stop(self):
        """Clean-shutdown contract: a long periodic sleep must notice
        ``stop()`` within one ~2s chunk, not after the full interval —
        otherwise every server shutdown burns the worker join timeout."""
        import time

        worker = TaskWorker(queue_manager=MagicMock(), task_handlers={})
        worker._running = True

        async def scenario():
            async def stop_soon():
                await asyncio.sleep(0.1)
                worker._running = False

            stopper = asyncio.create_task(stop_soon())
            start = time.monotonic()
            await worker._sleep_unless_stopped(60.0)
            await stopper
            return time.monotonic() - start

        elapsed = asyncio.run(scenario())
        assert elapsed < 5.0  # one chunk (~2s), never the full 60s
