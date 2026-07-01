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

"""
Background task worker for processing queued tasks.

The TaskWorker runs in a background thread with an internal asyncio event loop
that spawns one poller per pipeline stage. Each stage has its own semaphore so
a slow stage (e.g. transcribe) cannot block a fast one (e.g. clean).

Usage:
    from thestill.core.task_worker import TaskWorker
    from thestill.core.queue_manager import QueueManager, TaskStage
    from thestill.core.task_handlers import create_task_handlers

    queue_manager = QueueManager(db_path)
    handlers = create_task_handlers(app_state)

    worker = TaskWorker(
        queue_manager,
        handlers,
        parallel_jobs_per_stage={
            TaskStage.DOWNLOAD: 4,
            TaskStage.TRANSCRIBE: 1,
            TaskStage.CLEAN: 2,
        },
    )
    worker.start()

    # ... application runs ...

    worker.stop()  # Graceful shutdown
"""

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Callable, Dict, Optional

import structlog

from thestill.utils.exceptions import FatalError, TransientError

from .circuit_breaker import CircuitState, StageCircuitBreaker
from .error_classifier import classify_error_class
from .progress import ProgressCallback, ProgressUpdate
from .queue_manager import (
    QueueManager,
    Task,
    TaskStage,
    get_next_stages,
    is_entity_branch_stage,
    is_feed_scoped_stage,
    stages_at_or_before,
)

if TYPE_CHECKING:
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from .progress_store import ProgressStore

logger = structlog.get_logger(__name__)


class TaskWorker:
    """
    Background worker that polls the queue and processes tasks.

    Runs one poller coroutine per TaskStage, each bounded by its own
    semaphore. This means a slow transcribe task does not block a fast
    clean task — they pull from independent per-stage queues.

    Per-stage capacity is controlled by ``parallel_jobs_per_stage``. Any
    stage without an explicit entry falls back to ``parallel_jobs`` (legacy
    default).
    """

    # Default configuration
    DEFAULT_POLL_INTERVAL = 2.0  # Seconds between queue checks
    DEFAULT_STALE_TIMEOUT = 30  # Minutes before considering a task stale

    def __init__(
        self,
        queue_manager: QueueManager,
        task_handlers: Dict[TaskStage, Callable[[Task, ProgressCallback | None], None]],
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        stale_timeout_minutes: int = DEFAULT_STALE_TIMEOUT,
        progress_store: Optional["ProgressStore"] = None,
        repository: Optional["SqlitePodcastRepository"] = None,
        parallel_jobs: int = 1,
        parallel_jobs_per_stage: Optional[Dict[TaskStage, int]] = None,
        auto_heal_enabled: bool = False,
        heal_interval_s: float = 300.0,
        heal_cooldown_minutes: float = 10.0,
        max_heal_attempts: int = 2,
        circuit_breaker_enabled: bool = False,
        circuit_failure_threshold: int = 3,
        circuit_window_seconds: float = 120.0,
        circuit_cooldown_seconds: float = 60.0,
        watchdog_timeout_per_stage: Optional[Dict[TaskStage, Optional[float]]] = None,
    ):
        """
        Initialize task worker.

        Args:
            queue_manager: Queue manager for task operations
            task_handlers: Dict mapping TaskStage to handler function
            poll_interval: Seconds between queue polls when idle
            stale_timeout_minutes: Minutes before resetting stale tasks
            progress_store: Optional progress store for real-time progress updates
            repository: Optional repository for episode failure tracking
            parallel_jobs: Fallback per-stage capacity when a stage has no
                explicit entry in ``parallel_jobs_per_stage``.
            parallel_jobs_per_stage: Per-stage capacity overrides. Any stage
                omitted from this dict falls back to ``parallel_jobs``.
        """
        self.queue_manager = queue_manager
        self.task_handlers = task_handlers
        self.poll_interval = poll_interval
        self.stale_timeout_minutes = stale_timeout_minutes
        self.progress_store = progress_store
        self.repository = repository
        self.parallel_jobs = max(1, parallel_jobs)

        overrides = parallel_jobs_per_stage or {}
        self.parallel_jobs_per_stage: Dict[TaskStage, int] = {
            stage: max(1, overrides.get(stage, self.parallel_jobs)) for stage in TaskStage
        }

        # Spec #49 Layer 3 — auto-heal loop config. When enabled, a periodic
        # sweep requeues infra-class ``failed`` tasks once their dependency has
        # had time to recover, bounded per-task by ``max_heal_attempts``.
        self.auto_heal_enabled = auto_heal_enabled
        self.heal_interval_s = max(30.0, heal_interval_s)
        self.heal_cooldown_minutes = max(0.0, heal_cooldown_minutes)
        self.max_heal_attempts = max(0, max_heal_attempts)

        # Spec #49 Layer 1 — per-stage circuit breaker. When enabled, infra
        # failures that breach a threshold pause the stage's poller instead of
        # grinding every in-flight task to death against a dead dependency.
        self._breaker: Optional[StageCircuitBreaker] = (
            StageCircuitBreaker(
                failure_threshold=circuit_failure_threshold,
                window_seconds=circuit_window_seconds,
                cooldown_seconds=circuit_cooldown_seconds,
            )
            if circuit_breaker_enabled
            else None
        )

        # Spec #49 follow-up — per-stage handler watchdog. A handler that runs
        # past its stage's timeout is presumed wedged (e.g. a network socket
        # frozen by a host sleep with no lower-level timeout — the 2026-07-01
        # clean stall); the watchdog frees the stage's slot so it keeps
        # flowing. Absent config, every stage is None (disabled) to preserve
        # legacy behaviour for callers that don't wire it.
        overrides_wd = watchdog_timeout_per_stage or {}
        self._watchdog_timeout_s: Dict[TaskStage, Optional[float]] = {
            stage: overrides_wd.get(stage) for stage in TaskStage
        }

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Per-stage active tasks: stage -> {episode_id: Task}
        self._active_by_stage: Dict[TaskStage, Dict[str, Task]] = {stage: {} for stage in TaskStage}
        self._active_lock = threading.Lock()

    def start(self) -> None:
        """
        Start the worker thread.

        If the worker is already running, this is a no-op.
        """
        if self._running:
            logger.warning("task_worker_already_running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TaskWorker")
        self._thread.start()
        logger.info(
            "task_worker_started",
            parallel_jobs_per_stage={s.value: c for s, c in self.parallel_jobs_per_stage.items()},
        )

    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop the worker thread gracefully.

        Args:
            timeout: Maximum seconds to wait for current tasks to complete
        """
        if not self._running:
            logger.debug("task_worker_already_stopped")
            return

        self._running = False

        # Pollers exit on self._running check; calling loop.stop() here would
        # race with run_until_complete and raise at shutdown.

        if self._thread and self._thread.is_alive():
            logger.info("waiting_for_task_worker", action="finishing_current_tasks")
            self._thread.join(timeout=timeout)

            if self._thread.is_alive():
                logger.warning("task_worker_timeout", timeout_seconds=timeout)
            else:
                logger.info("task_worker_stopped")

    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running and (self._thread is not None and self._thread.is_alive())

    def get_current_task(self) -> Optional[Task]:
        """Get a task currently being processed (first active, for backward compat)."""
        with self._active_lock:
            for stage_active in self._active_by_stage.values():
                if stage_active:
                    return next(iter(stage_active.values()))
        return None

    @staticmethod
    def _task_key(task: Task) -> str:
        """Spec #48 — unique in-memory active-set key per task target.

        Episode tasks key by ``episode_id``; podcast-scoped (REFRESH_FEED)
        tasks key by ``podcast:<id>`` so multiple feed tasks (all with
        ``episode_id is None``) don't collapse onto a single ``None`` slot.
        """
        if task.episode_id is not None:
            return task.episode_id
        return f"podcast:{task.podcast_id}"

    def _all_active_episode_ids_locked(self) -> set[str]:
        """Return episode IDs active in any stage. Caller must hold _active_lock."""
        ids: set[str] = set()
        for stage_active in self._active_by_stage.values():
            for task in stage_active.values():
                if task.episode_id is not None:
                    ids.add(task.episode_id)
        return ids

    def _all_active_podcast_ids_locked(self) -> set[str]:
        """Return podcast IDs active in any feed-scoped stage (per-podcast
        mutex for REFRESH_FEED). Caller must hold _active_lock."""
        ids: set[str] = set()
        for stage_active in self._active_by_stage.values():
            for task in stage_active.values():
                if task.podcast_id is not None:
                    ids.add(task.podcast_id)
        return ids

    def get_status(self) -> dict:
        """Get worker status information, including per-stage utilization."""
        with self._active_lock:
            stages = {
                stage.value: {
                    "active": len(self._active_by_stage[stage]),
                    "capacity": self.parallel_jobs_per_stage[stage],
                }
                for stage in TaskStage
            }
            active_count = sum(s["active"] for s in stages.values())
        return {
            "running": self.is_running(),
            "parallel_jobs": self.parallel_jobs,
            "parallel_jobs_per_stage": {s.value: c for s, c in self.parallel_jobs_per_stage.items()},
            "active_episodes": active_count,
            "stages": stages,
            "poll_interval": self.poll_interval,
            # Spec #49 L1 — non-closed breakers only (empty when all healthy),
            # so the queue monitor can show which stages are paused on an outage.
            "circuit_breakers": (self._breaker.snapshot() if self._breaker is not None else {}),
        }

    def _run_loop(self) -> None:
        """Run the async event loop in the worker thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_worker_loop())
        finally:
            # Cancel remaining tasks
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()
            self._loop = None

    async def _async_worker_loop(self) -> None:
        """Spawn one poll loop per TaskStage and wait for them."""
        logger.info(
            "task_worker_loop_started",
            parallel_jobs_per_stage={s.value: c for s, c in self.parallel_jobs_per_stage.items()},
        )

        # Reset any stale tasks from previous runs on startup
        self._reset_stale_tasks()

        semaphores: Dict[TaskStage, asyncio.Semaphore] = {
            stage: asyncio.Semaphore(self.parallel_jobs_per_stage[stage]) for stage in TaskStage
        }
        pollers = [
            asyncio.create_task(self._supervised_stage_poll_loop(stage, semaphores[stage])) for stage in TaskStage
        ]
        # Long-running watchdog that catches tasks left in ``processing`` by
        # crashes or by the lock-race bookkeeping failure that motivated
        # ``_safe_schedule_retry``. Sweeps far less often than the stage
        # pollers — a wedged task only needs to recover eventually, not fast.
        pollers.append(asyncio.create_task(self._periodic_stale_task_reset()))
        # Spec #49 Layer 3 — the missing recovery loop over terminal states:
        # auto-requeue infra-class ``failed`` tasks once their dependency
        # recovers, so an outage that drained the retry budget self-heals
        # instead of waiting for a human to click "retry".
        if self.auto_heal_enabled:
            pollers.append(asyncio.create_task(self._periodic_terminal_heal()))

        try:
            await asyncio.gather(*pollers, return_exceptions=True)
        except asyncio.CancelledError:
            pass

        logger.info("task_worker_loop_ended")

    async def _periodic_stale_task_reset(self) -> None:
        """Periodically reclaim tasks stuck in ``processing``.

        The startup-only reset at ``_async_worker_loop`` entry handles
        server-restart recovery, but a task can also wedge mid-run when a
        post-handler bookkeeping write loses a SQLite lock race (the failure
        mode that produced this method). Sweeping at ``stale_timeout_minutes
        / 5`` keeps a wedged row out of ``processing`` for at most ~1.2× the
        stale timeout instead of "until next restart".
        """
        # Sweep at one-fifth of the stale window, clamped to [60s, 600s] so
        # tests with short stale timeouts still get a useful cadence and
        # default 30-min installs don't sweep every few seconds.
        interval_s = max(60.0, min(600.0, self.stale_timeout_minutes * 60 / 5))
        logger.info("stale_task_reset_poll_started", interval_s=interval_s)
        try:
            while self._running:
                await asyncio.sleep(interval_s)
                if not self._running:
                    break
                # ``_reset_stale_tasks`` already swallows exceptions; this
                # is belt-and-suspenders for the asyncio task itself.
                try:
                    self._reset_stale_tasks()
                except Exception as e:
                    logger.warning("periodic_stale_task_reset_error", error=str(e))
        finally:
            logger.info("stale_task_reset_poll_ended")

    async def _periodic_terminal_heal(self) -> None:
        """Periodically auto-requeue infra-class ``failed`` tasks (spec #49 L3).

        This is the recovery loop the queue previously lacked: once a shared
        dependency (DNS, the transcription runtime, a provider) recovers, the
        tasks it killed during the outage are reset to ``pending`` and flow
        back through the pipeline — no manual "retry" click. The cooldown keeps
        us from requeuing into a still-down dependency, and the per-task
        ``max_heal_attempts`` cap means a genuinely permanent failure stops
        looping and stays loudly terminal.
        """
        logger.info(
            "terminal_heal_poll_started",
            interval_s=self.heal_interval_s,
            cooldown_minutes=self.heal_cooldown_minutes,
            max_heal_attempts=self.max_heal_attempts,
        )
        try:
            while self._running:
                await asyncio.sleep(self.heal_interval_s)
                if not self._running:
                    break
                try:
                    self._heal_terminal_tasks()
                except Exception as e:
                    logger.warning("periodic_terminal_heal_error", error=str(e))
        finally:
            logger.info("terminal_heal_poll_ended")

    def _heal_terminal_tasks(self) -> int:
        """Run one auto-heal sweep; return the number of tasks requeued.

        Split out from the async loop so it can be unit-tested directly and so
        the loop body stays trivial. Swallows nothing the caller needs — the
        loop wraps it for belt-and-suspenders, but a healthy DB never raises.
        """
        from datetime import timedelta

        cooldown = timedelta(minutes=self.heal_cooldown_minutes)
        healable = self.queue_manager.find_healable_tasks(
            cooldown=cooldown,
            max_heal_attempts=self.max_heal_attempts,
        )
        if not healable:
            return 0

        healed = 0
        for task in healable:
            if self.queue_manager.heal_task(task.id, self.max_heal_attempts) is not None:
                healed += 1

        if healed:
            logger.info(
                "queue_auto_heal_swept",
                healed=healed,
                candidates=len(healable),
            )
        return healed

    async def _supervised_stage_poll_loop(self, stage: TaskStage, sem: asyncio.Semaphore) -> None:
        """Run a stage poller, restarting it if it ever crashes.

        The inner ``_stage_poll_loop`` already swallows per-iteration
        exceptions, but a defect that escaped that guard — or a ``BaseException``
        short of cancellation — would otherwise silently kill this stage's
        poller for the life of the process. That is exactly the single-stage
        silent-degradation failure mode: every other stage keeps draining while
        one goes dark until the next restart. This supervisor logs the crash
        and respawns the loop after a capped backoff so a stage can't stay dark.
        """
        backoff = 1.0
        while self._running:
            try:
                await self._stage_poll_loop(stage, sem)
                # A clean return means ``self._running`` went False — normal
                # shutdown, nothing to restart.
                return
            except asyncio.CancelledError:
                raise
            except BaseException as e:  # noqa: BLE001 — supervisor must catch all
                logger.exception(
                    "stage_poll_loop_crashed",
                    stage=stage.value,
                    error=str(e),
                    restart_in_s=backoff,
                    note="respawning poller",
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _stage_poll_loop(self, stage: TaskStage, sem: asyncio.Semaphore) -> None:
        """Poll the queue for a single stage and dispatch up to its capacity."""
        capacity = self.parallel_jobs_per_stage[stage]
        active = self._active_by_stage[stage]
        logger.info("stage_poll_loop_started", stage=stage.value, capacity=capacity)

        while self._running:
            try:
                with self._active_lock:
                    slots = capacity - len(active)
                    exclude_eps = self._all_active_episode_ids_locked() or None
                    exclude_pods = self._all_active_podcast_ids_locked() or None

                if slots > 0:
                    for _ in range(slots):
                        # Spec #49 L1 — gate on the breaker. When OPEN the call
                        # returns False and the stage pauses; when it promotes
                        # to HALF_OPEN it reserves a single probe slot that we
                        # MUST release (cancel_dispatch) if the queue is empty.
                        if self._breaker is not None and not self._breaker.allow_dispatch(stage):
                            break

                        task = self.queue_manager.get_next_task(
                            stage=stage,
                            exclude_episode_ids=exclude_eps,
                            exclude_podcast_ids=exclude_pods,
                        )
                        if task is None:
                            if self._breaker is not None:
                                self._breaker.cancel_dispatch(stage)
                            break

                        key = self._task_key(task)
                        with self._active_lock:
                            # Recheck under lock: another stage may have claimed this
                            # target between the poll and now. Same-episode cross-stage
                            # concurrency would race on transcript/summary artifacts;
                            # same-podcast feed tasks would double-fetch.
                            if any(key in s for s in self._active_by_stage.values()):
                                # Another stage claimed this target; release the
                                # probe slot we may have reserved so it isn't
                                # leaked, then move on.
                                if self._breaker is not None:
                                    self._breaker.cancel_dispatch(stage)
                                continue
                            active[key] = task
                            exclude_eps = self._all_active_episode_ids_locked() or None
                            exclude_pods = self._all_active_podcast_ids_locked() or None

                        asyncio.create_task(self._process_task_async(task, sem, stage))

                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.exception(
                    "unexpected_error_in_stage_loop",
                    stage=stage.value,
                    error=str(e),
                    exc_info=True,
                )
                await asyncio.sleep(self.poll_interval)

        logger.info("stage_poll_loop_ended", stage=stage.value)

    async def _process_task_async(self, task: Task, sem: asyncio.Semaphore, stage: TaskStage) -> None:
        """Process a task in a thread, bounded by the stage's semaphore.

        A per-stage watchdog (``_watchdog_timeout_s``) caps how long the handler
        may block. On timeout we free the semaphore + active slot so the stage
        keeps flowing even though the abandoned worker thread may still be alive
        (Python can't force-kill it) — the canonical trigger is a network call
        frozen by a host sleep with no lower-level timeout. We deliberately do
        NOT reschedule the row here: it stays ``processing`` and the periodic
        stale-task reset requeues it, which avoids racing the abandoned thread
        if it later revives and writes its own completion/retry.
        """
        async with sem:
            try:
                timeout = self._watchdog_timeout_s.get(stage)
                if timeout is not None and timeout > 0:
                    await asyncio.wait_for(asyncio.to_thread(self._process_task, task), timeout=timeout)
                else:
                    await asyncio.to_thread(self._process_task, task)
            except (asyncio.TimeoutError, TimeoutError):
                logger.error(
                    "task_handler_timeout",
                    stage=stage.value,
                    task_id=task.id,
                    episode_id=task.episode_id,
                    timeout_s=self._watchdog_timeout_s.get(stage),
                    note="freeing worker slot; stale-task reset will requeue the abandoned row",
                )
            finally:
                with self._active_lock:
                    self._active_by_stage[stage].pop(self._task_key(task), None)

    def _process_task(self, task: Task) -> None:
        """
        Process a single task using the appropriate handler.

        Error handling:
        - TransientError: Schedule retry with exponential backoff
        - FatalError: Move to Dead Letter Queue (DLQ)
        - Other exceptions: Treat as transient (schedule retry)

        On success, if task has run_full_pipeline metadata, enqueue next stage.

        Args:
            task: Task to process
        """
        # Bind correlation ID for task processing
        import uuid

        worker_id = str(uuid.uuid4())[:8]
        structlog.contextvars.bind_contextvars(
            worker_id=worker_id,
            task_id=task.id,
            episode_id=task.episode_id,
            stage=task.stage.value,
            retry_count=task.retry_count,
        )

        try:
            handler = self.task_handlers.get(task.stage)

            if not handler:
                error_msg = f"No handler registered for stage: {task.stage.value}"
                logger.error("no_handler_for_stage", stage=task.stage.value)
                self.queue_manager.mark_dead(task.id, error_msg)
                return

            logger.info("task_processing_started")

            # Create progress callback if progress store is available
            progress_callback: ProgressCallback | None = None
            if self.progress_store:

                def progress_callback(update: ProgressUpdate) -> None:
                    self.progress_store.update_from_callback(task.id, update)

            try:
                # Execute the handler with optional progress callback
                handler(task, progress_callback)

                # Handler completed successfully - mark task complete
                self.queue_manager.complete_task(task.id)
                logger.info("task_completed_successfully")

                # Spec #49 L1 — a success closes the stage's breaker (and is the
                # signal that a half-open probe passed: dependency recovered).
                if self._breaker is not None:
                    self._breaker.record_success(task.stage.value)

                # Feed-scoped (REFRESH_FEED) tasks have no episode and do
                # their own dynamic DOWNLOAD fan-out inside the handler;
                # skip the episode-chain bookkeeping entirely.
                if task.episode_id is not None:
                    # Auto-resolve any stale DLQ rows for this episode at the
                    # same stage or earlier in the same branch. After a user
                    # fixes (e.g.) a bad API key and reruns transcription,
                    # the old dead transcribe row is obsolete — keeping it
                    # around just trains users to ignore the queue.
                    self.queue_manager.supersede_stale_tasks(task.episode_id, task.stage)

                    # The episode-level failure banner (failed_at_stage /
                    # failure_reason) is what drives the inbox "Retry"
                    # affordance — superseding the dead queue rows above does
                    # not touch it. Clear it too when this success makes it
                    # moot, scoped to the same-branch stages at or before the
                    # one that just completed so a success here can't wipe a
                    # failure recorded at a later, not-yet-rerun stage.
                    if self.repository is not None:
                        self.repository.clear_episode_failure_for_stages(
                            task.episode_id,
                            [s.value for s in stages_at_or_before(task.stage)],
                        )

                    # Chain enqueue next stage if running full pipeline
                    self._maybe_enqueue_next_stage(task)

            except FatalError as e:
                # Fatal error - move to DLQ, no retry
                error_msg = str(e)
                logger.error(
                    "task_fatal_error",
                    error=error_msg,
                    destination="dlq",
                    exc_info=True,
                )
                self._safe_mark_dead(task, error_msg, "fatal")
                self._mark_episode_failed(task, error_msg, "fatal")
                self._report_failure(task.id, error_msg)

            except TransientError as e:
                # Transient error - schedule retry with backoff. Spec #49:
                # attribute it as 'infra' (shared dependency down → healable)
                # vs 'item' (this work is bad → manual retry) so an outage that
                # drains the retry budget can self-recover once it clears.
                error_msg = str(e)
                error_class = classify_error_class(e)
                logger.warning(
                    "task_transient_error",
                    error=error_msg,
                    error_class=error_class,
                    will_retry=True,
                    exc_info=True,
                )
                updated_task = self._handle_transient_failure(task, error_msg, error_class)
                # Check if retries exhausted (task marked as failed)
                if updated_task and updated_task.status.value == "failed":
                    self._mark_episode_failed(task, error_msg, "transient")
                self._report_failure(task.id, error_msg)

            except Exception as e:
                # Unknown exception - treat as transient and retry
                error_msg = str(e)
                error_class = classify_error_class(e)
                logger.exception(
                    "task_unexpected_error",
                    error=error_msg,
                    error_class=error_class,
                    exc_info=True,
                )
                updated_task = self._handle_transient_failure(task, error_msg, error_class)
                # Check if retries exhausted (task marked as failed)
                if updated_task and updated_task.status.value == "failed":
                    self._mark_episode_failed(task, error_msg, "transient")
                self._report_failure(task.id, error_msg)

            finally:
                # Clean up progress store after a delay to allow final updates to be delivered
                if self.progress_store:
                    # Schedule cleanup in a separate thread to avoid blocking
                    def cleanup():
                        time.sleep(5.0)  # Allow 5 seconds for clients to receive final update
                        self.progress_store.cleanup(task.id)

                    threading.Thread(target=cleanup, daemon=True).start()

        finally:
            # Clear correlation context
            structlog.contextvars.clear_contextvars()

    # Map stage (verb) to resulting episode state (past participle). Spec #28
    # entity-branch stages are intentionally absent — they don't progress
    # the user-facing ``EpisodeState`` machine, and ``target_state`` early-
    # exit only applies to the user-facing chain that ends in
    # ``summarize``→``summarized``. ``.get(...)`` returning ``None`` for
    # entity-branch stages is the correct no-op behaviour here.
    _STAGE_TO_STATE: Dict[str, str] = {
        "download": "downloaded",
        "downsample": "downsampled",
        "transcribe": "transcribed",
        "clean": "cleaned",
        "summarize": "summarized",
    }

    def _maybe_enqueue_next_stage(self, task: Task) -> None:
        """Advance the pipeline chain by one step.

        The chain is purely linear (spec #28 §0.5):
        ``download → downsample → transcribe → clean → summarize →
        extract-entities → resolve-entities → reindex``.

        Two distinct chaining policies, gated independently per
        successor:

        - **User-chain successors** (``download``…``summarize``): only
          chain when ``run_full_pipeline`` is set, so single-stage
          retry buttons and admin re-runs don't accidentally re-process
          a whole episode. ``target_state`` is honoured as an early-
          stop marker — callers passing ``target_state="summarized"``
          stop after the user chain.
        - **Entity-branch successors** (``extract-entities``,
          ``resolve-entities``, ``reindex``): ALWAYS chain. The entity
          stages are idempotent + atomic; search and discovery rely on
          them. After a successful summarize the user expects the
          episode to be fully indexed regardless of how the summarize
          task was enqueued (CLI, retry button, admin re-run, queue).
          Standalone ``extract-entities`` enqueues (admin rebuilds,
          repair scripts) also need this auto-chain so mentions land
          as ``resolved`` and the index gets the embeddings.
        """
        in_entity_branch = is_entity_branch_stage(task.stage)
        allow_user_chain = in_entity_branch or bool(task.metadata.get("run_full_pipeline"))

        target_state = task.metadata.get("target_state")
        resulting_state = self._STAGE_TO_STATE.get(task.stage.value)
        target_reached = target_state is not None and resulting_state == target_state

        successors = get_next_stages(task.stage)
        if target_reached or not allow_user_chain:
            # Filter to entity-branch successors only. ``target_reached``
            # reflects an explicit early-stop on the user chain; the
            # ``allow_user_chain`` gate handles single-stage retries
            # that didn't opt into the full pipeline. In both cases
            # the entity branch is non-destructive and should still
            # run so the corpus stays consistent.
            successors = [s for s in successors if is_entity_branch_stage(s)]
        if not successors:
            log_msg = "pipeline_target_reached" if target_reached else "pipeline_complete"
            logger.info(log_msg, stage=task.stage.value, target_state=target_state)
            return

        for next_stage in successors:
            logger.info(
                "chain_enqueueing_next_stage",
                from_stage=task.stage.value,
                next_stage=next_stage.value,
            )
            self.queue_manager.add_task(
                episode_id=task.episode_id,
                stage=next_stage,
                priority=task.priority,
                metadata=task.metadata,
            )

    def _report_failure(self, task_id: str, error_msg: str) -> None:
        """Report task failure via progress store if available."""
        if self.progress_store:
            from .progress import TranscriptionStage

            self.progress_store.update_from_callback(
                task_id,
                ProgressUpdate(
                    stage=TranscriptionStage.FAILED,
                    progress_pct=0,
                    message=f"Task failed: {error_msg}",
                ),
            )

    def _mark_episode_failed(self, task: Task, error_msg: str, failure_type: str) -> None:
        """Persist a final task failure on the owning episode.

        Spec #28 §6 ("Failure isolation rule") — for entity-branch
        stages we write to ``episodes.entity_extraction_status='failed'``
        rather than ``failed_at_stage``. Setting ``failed_at_stage`` would
        flip the episode's user-visible state to ``FAILED`` and render it
        as a red card, even though the episode itself was downloaded,
        transcribed and (likely) summarised successfully — only its
        entity index is incomplete.
        """
        if not self.repository:
            logger.debug("No repository configured, skipping episode failure tracking")
            return

        try:
            if is_feed_scoped_stage(task.stage):
                # Spec #48 — feed-scoped failure domain. There is no episode
                # to mark; write podcast-level failure state and PARK the feed
                # (terminal here: this is only called once retries are
                # exhausted / on a fatal error), so the scheduler stops
                # re-enqueuing it. Operator retry re-arms it. Cache headers are
                # untouched (FM-2 is enforced on the feed path).
                self.repository.record_refresh_error(
                    podcast_id=task.podcast_id,
                    error=error_msg,
                    terminal=True,
                )
                logger.info(
                    "refresh_feed_failed_parked",
                    stage=task.stage.value,
                    failure_type=failure_type,
                    podcast_id=task.podcast_id,
                )
                return

            if is_entity_branch_stage(task.stage):
                # Entity-branch failures live in their own status column;
                # ``failed_at_stage`` and the episode-card UX stay
                # untouched.
                self.repository.update_entity_extraction_status(
                    episode_id=task.episode_id,
                    status="failed",
                )
                logger.info(
                    "entity_branch_failed",
                    stage=task.stage.value,
                    failure_type=failure_type,
                    episode_id=task.episode_id,
                )
                return

            self.repository.mark_episode_failed(
                episode_id=task.episode_id,
                failed_at_stage=task.stage.value,
                failure_reason=error_msg,
                failure_type=failure_type,
            )
        except Exception as e:
            # Don't fail the entire operation if episode marking fails
            logger.error(f"Failed to mark episode {task.episode_id} as failed: {e}")

    def _reset_stale_tasks(self) -> None:
        """Reset any stale processing tasks from previous runs."""
        try:
            reset_count = self.queue_manager.reset_stale_tasks(self.stale_timeout_minutes)
            if reset_count > 0:
                logger.info(f"Reset {reset_count} stale tasks on startup")
        except Exception as e:
            logger.warning(f"Failed to reset stale tasks: {e}")

    def _handle_transient_failure(self, task: Task, error_msg: str, error_class: Optional[str]) -> Optional[Task]:
        """Route a transient/unknown failure: spend retry budget, or — when an
        infra failure trips the stage breaker — park it without spending budget.

        Spec #49 L1+L2 join here: the breaker decides *whether the stage runs*,
        and for an infra failure that has tripped it we must NOT charge the
        item's ``max_retries`` (the failure is the dependency's fault). The task
        is rescheduled eligible-immediately; the breaker keeps the poller from
        re-dispatching it until a half-open probe confirms recovery.
        """
        if self._breaker is not None and error_class == "infra":
            state = self._breaker.record_failure(task.stage.value)
            if state is not CircuitState.CLOSED:
                logger.warning(
                    "task_parked_circuit_open",
                    stage=task.stage.value,
                    circuit_state=state.value,
                    error=error_msg,
                    note="not charging retry budget while dependency is down",
                )
                return self.queue_manager.reschedule_without_budget(task.id, error_msg, error_class)
        return self._safe_schedule_retry(task, error_msg, error_class)

    def _safe_schedule_retry(self, task: Task, error_msg: str, error_class: Optional[str] = None) -> Optional[Task]:
        """Schedule a retry without leaving the row stuck in ``processing``.

        Two failure modes the naive call can't survive:

        1. The handler succeeded but a post-handler bookkeeping write
           (``complete_task`` / ``add_task``) raised. Calling
           ``schedule_retry`` here would re-run a stage whose work is
           already on disk. If the row is already ``completed`` we skip
           the retry entirely.
        2. ``schedule_retry`` itself loses a SQLite lock race even after
           the in-method retry helper exhausts its budget. We swallow
           the exception and rely on the periodic stale-task reset
           (see ``_periodic_stale_task_reset``) to recover the row.
        """
        try:
            current = self.queue_manager.get_task(task.id)
        except Exception as e:
            logger.warning("safe_schedule_retry_lookup_failed", task_id=task.id, error=str(e))
            current = None

        if current is not None and current.status.value == "completed":
            logger.warning(
                "task_post_handler_bookkeeping_failed",
                task_id=task.id,
                stage=task.stage.value,
                note="handler already marked task completed; not rescheduling",
                error=error_msg,
            )
            return current

        try:
            return self.queue_manager.schedule_retry(task.id, error_msg, error_class)
        except Exception as e:
            logger.error(
                "schedule_retry_failed",
                task_id=task.id,
                stage=task.stage.value,
                original_error=error_msg,
                schedule_error=str(e),
                note="stale-task reset will recover this row",
                exc_info=True,
            )
            return None

    def _safe_mark_dead(self, task: Task, error_msg: str, error_class: Optional[str] = None) -> None:
        """``mark_dead`` analogue of ``_safe_schedule_retry``: never leave processing."""
        try:
            self.queue_manager.mark_dead(task.id, error_msg, error_class)
        except Exception as e:
            logger.error(
                "mark_dead_failed",
                task_id=task.id,
                stage=task.stage.value,
                original_error=error_msg,
                mark_dead_error=str(e),
                note="stale-task reset will recover this row",
                exc_info=True,
            )
