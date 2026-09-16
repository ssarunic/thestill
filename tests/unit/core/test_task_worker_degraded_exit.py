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

"""A degraded worker must get the process restarted, not just fail readiness.

2026-09-16: production sat for days with ``/health/ready`` returning 503
("leaked handler threads; restart required") while the queue backed up.
Docker's restart policy only reacts to process exit, so the worker now
asks for one when it crosses the abandonment budget.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from unittest.mock import MagicMock

from thestill.core import task_worker as tw
from thestill.core.queue_manager import Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker, request_process_exit


def _task(n: int) -> Task:
    return Task(id=f"task-{n}", episode_id=f"episode-{n}", stage=TaskStage.CLEAN, status=TaskStatus.PROCESSING)


def _wedge_until(release: threading.Event):
    def _hang(_task):
        release.wait(timeout=5.0)

    return _hang


def test_on_degraded_fires_exactly_once_when_the_budget_is_spent(monkeypatch):
    monkeypatch.setenv("QUEUE_ABANDONED_THREAD_BUDGET", "2")
    fired: list[int] = []
    release = threading.Event()
    worker = TaskWorker(
        queue_manager=MagicMock(),
        task_handlers={},
        watchdog_timeout_per_stage={TaskStage.CLEAN: 0.05},
        on_degraded=lambda: fired.append(1),
    )
    worker._process_task = _wedge_until(release)  # type: ignore[method-assign]

    async def run():
        sem = asyncio.Semaphore(3)
        for n in range(3):  # three abandonments against a budget of two
            task = _task(n)
            worker._active_by_stage[TaskStage.CLEAN][task.episode_id] = task
            await worker._process_task_async(task, sem, TaskStage.CLEAN)
        release.set()

    asyncio.run(run())
    assert worker.is_degraded() is True
    assert fired == [1]  # once at the transition, not again on the third leak


def test_worker_without_hook_only_degrades(monkeypatch):
    monkeypatch.setenv("QUEUE_ABANDONED_THREAD_BUDGET", "1")
    release = threading.Event()
    worker = TaskWorker(queue_manager=MagicMock(), task_handlers={}, watchdog_timeout_per_stage={TaskStage.CLEAN: 0.05})
    worker._process_task = _wedge_until(release)  # type: ignore[method-assign]

    async def run():
        task = _task(0)
        worker._active_by_stage[TaskStage.CLEAN][task.episode_id] = task
        await worker._process_task_async(task, asyncio.Semaphore(1), TaskStage.CLEAN)
        release.set()

    asyncio.run(run())
    assert worker.is_degraded() is True


def test_a_failing_hook_does_not_take_the_loop_down(monkeypatch):
    monkeypatch.setenv("QUEUE_ABANDONED_THREAD_BUDGET", "1")
    release = threading.Event()

    def _boom():
        raise RuntimeError("hook exploded")

    worker = TaskWorker(
        queue_manager=MagicMock(),
        task_handlers={},
        watchdog_timeout_per_stage={TaskStage.CLEAN: 0.05},
        on_degraded=_boom,
    )
    worker._process_task = _wedge_until(release)  # type: ignore[method-assign]

    async def run():
        task = _task(0)
        worker._active_by_stage[TaskStage.CLEAN][task.episode_id] = task
        await worker._process_task_async(task, asyncio.Semaphore(1), TaskStage.CLEAN)
        release.set()

    asyncio.run(run())  # no exception escapes
    assert worker.is_degraded() is True


def test_request_process_exit_sends_sigterm_to_self_after_the_delay(monkeypatch):
    sent: list[tuple[int, int]] = []
    done = threading.Event()

    def _kill(pid, sig):
        sent.append((pid, sig))
        done.set()

    monkeypatch.setattr(tw.os, "kill", _kill)
    request_process_exit(delay_s=0.01)
    assert done.wait(timeout=2.0)
    assert sent == [(tw.os.getpid(), signal.SIGTERM)]


def test_exit_on_degraded_is_on_by_default_and_can_be_disabled(monkeypatch):
    from thestill.utils.config import is_queue_exit_on_degraded_enabled

    monkeypatch.delenv("QUEUE_EXIT_ON_DEGRADED", raising=False)
    assert is_queue_exit_on_degraded_enabled() is True
    monkeypatch.setenv("QUEUE_EXIT_ON_DEGRADED", "false")
    assert is_queue_exit_on_degraded_enabled() is False
