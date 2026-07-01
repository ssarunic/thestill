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

"""Worker resilience against a wedged handler and a crashed stage poller.

Both defend the same failure the 2026-07-01 ``clean`` stall exposed: a host
sleep froze the clean handler's network socket with no lower-level timeout, so
the worker thread hung forever, permanently holding its stage's semaphore slot
while every other stage kept draining.

- The per-stage watchdog (``_process_task_async``) abandons a handler that
  blocks past its timeout and frees the slot, so the stage keeps flowing.
- The poller supervisor (``_supervised_stage_poll_loop``) restarts a stage
  loop that crashes instead of letting it go silently dark.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker


def _make_task(stage: TaskStage = TaskStage.CLEAN) -> Task:
    return Task(
        id="task-uuid",
        episode_id="episode-uuid",
        stage=stage,
        status=TaskStatus.PROCESSING,
    )


class TestHandlerWatchdog:
    def test_wedged_handler_frees_slot_without_hanging(self):
        """A handler that blocks past the watchdog must not wedge the stage.

        The abandoned thread may still be alive, but the active-set slot is
        released so the poller can dispatch other work.
        """
        release = threading.Event()

        def _hang(_task):
            # Simulates a frozen network socket: blocks well past the watchdog.
            release.wait(timeout=5.0)

        worker = TaskWorker(
            queue_manager=MagicMock(),
            task_handlers={},
            watchdog_timeout_per_stage={TaskStage.CLEAN: 0.05},
        )
        worker._process_task = _hang  # type: ignore[method-assign]
        task = _make_task()
        worker._active_by_stage[TaskStage.CLEAN][task.episode_id] = task

        async def run():
            sem = asyncio.Semaphore(1)
            await worker._process_task_async(task, sem, TaskStage.CLEAN)
            # The semaphore must be fully released for the next dispatch.
            assert sem._value == 1
            # Release the abandoned thread now so event-loop teardown (which
            # joins the default executor) doesn't block on it.
            release.set()

        asyncio.run(run())

        # Slot freed even though the handler never returned in time.
        assert task.episode_id not in worker._active_by_stage[TaskStage.CLEAN]

    def test_no_watchdog_lets_handler_complete_and_frees_slot(self):
        """With the watchdog disabled (default), a normal handler runs to
        completion and its slot is released as before."""
        handler = MagicMock()
        worker = TaskWorker(queue_manager=MagicMock(), task_handlers={})  # no watchdog config
        worker._process_task = handler  # type: ignore[method-assign]
        task = _make_task()
        worker._active_by_stage[TaskStage.CLEAN][task.episode_id] = task

        async def run():
            sem = asyncio.Semaphore(1)
            await worker._process_task_async(task, sem, TaskStage.CLEAN)

        asyncio.run(run())

        handler.assert_called_once_with(task)
        assert task.episode_id not in worker._active_by_stage[TaskStage.CLEAN]


class TestPollerSupervisor:
    def test_restarts_crashed_poller(self, monkeypatch):
        """A stage loop that raises must be respawned, not left dark."""
        worker = TaskWorker(queue_manager=MagicMock(), task_handlers={})
        calls = {"n": 0}

        async def flaky_loop(_stage, _sem):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("poller boom")
            # Second run: simulate normal shutdown so the supervisor returns.
            worker._running = False

        worker._stage_poll_loop = flaky_loop  # type: ignore[method-assign]

        # No real backoff delay.
        async def no_sleep(_seconds):
            return None

        monkeypatch.setattr("thestill.core.task_worker.asyncio.sleep", no_sleep)

        worker._running = True
        asyncio.run(worker._supervised_stage_poll_loop(TaskStage.CLEAN, asyncio.Semaphore(1)))

        # Crashed once, restarted once, then exited cleanly.
        assert calls["n"] == 2

    def test_supervisor_exits_cleanly_when_not_running(self, monkeypatch):
        """A clean return from the loop (shutdown) must not respawn it."""
        worker = TaskWorker(queue_manager=MagicMock(), task_handlers={})
        calls = {"n": 0}

        async def clean_loop(_stage, _sem):
            calls["n"] += 1
            worker._running = False

        worker._stage_poll_loop = clean_loop  # type: ignore[method-assign]
        worker._running = True
        asyncio.run(worker._supervised_stage_poll_loop(TaskStage.CLEAN, asyncio.Semaphore(1)))

        assert calls["n"] == 1
