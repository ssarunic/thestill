"""The handler thread pool must be at least as large as what we promise.

2026-08-07 outage. ``TaskWorker`` dispatches every handler through
``asyncio.to_thread``, which uses the loop's DEFAULT executor — sized
``min(32, cpu_count + 4)``, i.e. 6 threads on the 2-vCPU deployment box.
Per-stage capacities summed to 28. The queue therefore claimed tasks that had
no thread to run on: they sat in ``processing`` looking alive and did nothing.

It compounded through the watchdog. A handler that outran its budget was
abandoned — Python cannot kill a thread — so it kept its executor slot while a
replacement was claimed into the slot it vacated. At ~21 abandonments/hour
every thread ended up held by abandoned work and all nine stages froze
simultaneously, transcription included, so Dalston went idle. Liveness probes
stayed green the whole time because ``/health`` never looks at the worker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from thestill.core.queue_manager import TaskStage
from thestill.core.task_worker import TaskWorker
from thestill.utils.config import get_stage_watchdog_seconds


def _worker(capacities: dict | None = None) -> TaskWorker:
    """Build a worker with explicit per-TaskStage capacities.

    Keyed by ``TaskStage``, deliberately: an earlier version took **kwargs and
    looked them up by ``stage.value``, so ``refresh_feed=3`` never matched the
    stage whose value is ``refresh-feed`` and silently configured 1 instead.
    The test then passed while measuring something other than what it claimed.
    """
    caps = capacities or {}
    return TaskWorker(
        queue_manager=MagicMock(),
        task_handlers={},
        parallel_jobs_per_stage={stage: caps.get(stage, 1) for stage in TaskStage},
    )


class TestExecutorSizing:
    def test_pool_is_at_least_the_sum_of_stage_capacities(self):
        worker = _worker({TaskStage.TRANSCRIBE: 5, TaskStage.CLEAN: 5, TaskStage.SUMMARIZE: 5})
        assert worker.executor_max_workers() >= sum(worker.parallel_jobs_per_stage.values()), (
            "the executor must run every task the queue may claim; otherwise claims "
            "pile up in `processing` with no thread behind them"
        )

    def test_pool_uses_the_real_production_capacities(self):
        # Spec #66 values. Keyed by TaskStage so `refresh-feed` actually lands.
        worker = _worker(
            {
                TaskStage.TRANSCRIBE: 5,
                TaskStage.CLEAN: 5,
                TaskStage.SUMMARIZE: 5,
                TaskStage.DOWNLOAD: 3,
                TaskStage.REFRESH_FEED: 3,
                TaskStage.REINDEX: 2,
            }
        )
        assert worker.parallel_jobs_per_stage[TaskStage.REFRESH_FEED] == 3, "capacity key did not apply"
        assert worker.executor_max_workers() >= sum(worker.parallel_jobs_per_stage.values())

    def test_pool_absorbs_the_abandonment_budget(self):
        # Headroom alone only defers the outage: every abandonment permanently
        # consumes a slot. The pool must cover the budget it tolerates.
        worker = _worker({TaskStage.TRANSCRIBE: 5})
        promised = sum(worker.parallel_jobs_per_stage.values())
        assert worker.executor_max_workers() >= promised + worker.abandoned_thread_budget

    def test_the_loop_actually_installs_that_executor(self):
        """The number is worthless if the loop never receives it."""
        import asyncio

        worker = _worker({TaskStage.TRANSCRIBE: 5, TaskStage.CLEAN: 5})
        installed = {}

        async def run():
            loop = asyncio.get_running_loop()
            original = loop.set_default_executor

            def spy(executor):
                installed["max_workers"] = executor._max_workers
                original(executor)

            loop.set_default_executor = spy  # type: ignore[method-assign]
            worker._running = True
            worker.queue_manager.get_next_task.return_value = None
            # ``_run_loop`` is the sync wrapper that owns its own event loop;
            # the sizing lives in the async body.
            task = asyncio.create_task(worker._async_worker_loop())
            await asyncio.sleep(0.25)
            worker._running = False
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        asyncio.run(run())
        assert (
            installed.get("max_workers") == worker.executor_max_workers()
        ), f"loop received {installed.get('max_workers')}, expected {worker.executor_max_workers()}"


class TestDegradedGuard:
    def test_not_degraded_before_the_budget_is_spent(self):
        worker = _worker()
        assert worker.is_degraded() is False

    def test_degraded_once_leaks_reach_the_budget(self):
        worker = _worker()
        worker._abandoned_threads[TaskStage.REINDEX] = worker.abandoned_thread_budget
        assert worker.is_degraded() is True

    def test_degraded_worker_reports_unready(self):
        # The outage ran for hours with readiness green. A frozen pipeline
        # behind a healthy web server is not ready.
        from thestill.web.services.health_service import HealthService

        worker = _worker()
        worker._abandoned_threads[TaskStage.REINDEX] = worker.abandoned_thread_budget
        svc = HealthService(MagicMock())
        try:
            svc.check_worker(worker)
        except RuntimeError as exc:
            assert "degraded" in str(exc)
        else:
            raise AssertionError("a degraded worker must fail readiness")

    def test_healthy_worker_passes_readiness(self):
        from thestill.web.services.health_service import HealthService

        HealthService(MagicMock()).check_worker(_worker())

    def test_absent_worker_is_tolerated(self):
        # Hand-built fixtures may not wire one.
        from thestill.web.services.health_service import HealthService

        HealthService(MagicMock()).check_worker(None)


class TestWatchdogBudgets:
    """The watchdog frees slots from handlers that will NEVER finish. It is
    not a performance budget — abandoning work that would have completed
    leaks a thread, because the handler cannot be killed."""

    def test_cpu_bound_stages_get_generous_budgets(self):
        budgets = {s.value: v for s, v in get_stage_watchdog_seconds().items()}
        for stage in ("reindex", "compute-related", "rebuild-cooccurrences"):
            assert budgets[stage] is None or budgets[stage] >= 7200, (
                f"{stage} is CPU-bound on a small instance; a short budget abandons "
                "work that would have finished and leaks its executor thread"
            )

    def test_transcribe_stays_unbounded(self):
        assert get_stage_watchdog_seconds()[TaskStage.TRANSCRIBE] is None

    def test_global_kill_switch_still_disables_everything(self, monkeypatch):
        monkeypatch.setenv("QUEUE_STAGE_WATCHDOG_SECONDS", "0")
        assert all(v is None for v in get_stage_watchdog_seconds().values())

    def test_uniform_override_still_applies(self, monkeypatch):
        monkeypatch.setenv("QUEUE_STAGE_WATCHDOG_SECONDS", "900")
        assert all(v == 900.0 for v in get_stage_watchdog_seconds().values())
