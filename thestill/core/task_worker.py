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

The TaskWorker runs in a background thread with an internal asyncio event loop,
polling the queue for pending tasks and executing them using the appropriate
handlers. It supports parallel job processing controlled by the parallel_jobs
parameter — when set to 1 (default), tasks execute sequentially as before.

Usage:
    from thestill.core.task_worker import TaskWorker
    from thestill.core.queue_manager import QueueManager, TaskStage
    from thestill.core.task_handlers import create_task_handlers

    queue_manager = QueueManager(db_path)
    handlers = create_task_handlers(app_state)

    worker = TaskWorker(queue_manager, handlers, parallel_jobs=2)
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

from .progress import ProgressCallback, ProgressUpdate
from .queue_manager import QueueManager, Task, TaskStage, get_next_stage

if TYPE_CHECKING:
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from .progress_store import ProgressStore

logger = structlog.get_logger(__name__)


class TaskWorker:
    """
    Background worker that polls the queue and processes tasks.

    Supports concurrent processing of multiple episodes via parallel_jobs.
    Each job processes one episode through its pipeline stages sequentially,
    but multiple jobs can run in parallel (e.g., one episode transcribing on
    Dalston while another is cleaning on Gemini).

    When parallel_jobs=1, behavior is identical to sequential processing.
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
            parallel_jobs: Max episodes to process in parallel (1 = sequential)
        """
        self.queue_manager = queue_manager
        self.task_handlers = task_handlers
        self.poll_interval = poll_interval
        self.stale_timeout_minutes = stale_timeout_minutes
        self.progress_store = progress_store
        self.repository = repository
        self.parallel_jobs = max(1, parallel_jobs)

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._active_tasks: dict[str, Task] = {}  # episode_id → Task being processed
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
        logger.info("task_worker_started", parallel_jobs=self.parallel_jobs)

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

        # Signal the event loop to stop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

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
            if self._active_tasks:
                return next(iter(self._active_tasks.values()))
        return None

    def get_status(self) -> dict:
        """Get worker status information."""
        with self._active_lock:
            active_count = len(self._active_tasks)
        return {
            "running": self.is_running(),
            "parallel_jobs": self.parallel_jobs,
            "active_episodes": active_count,
            "poll_interval": self.poll_interval,
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
        """Main async worker loop that polls and dispatches tasks."""
        logger.info("task_worker_loop_started", parallel_jobs=self.parallel_jobs)

        # Reset any stale tasks from previous runs on startup
        self._reset_stale_tasks()

        sem = asyncio.Semaphore(self.parallel_jobs)

        while self._running:
            try:
                with self._active_lock:
                    slots = self.parallel_jobs - len(self._active_tasks)
                    exclude = set(self._active_tasks.keys()) if self._active_tasks else None

                if slots > 0:
                    for _ in range(slots):
                        task = self.queue_manager.get_next_task(exclude_episode_ids=exclude)
                        if task is None:
                            break

                        with self._active_lock:
                            if task.episode_id in self._active_tasks:
                                continue
                            self._active_tasks[task.episode_id] = task
                            exclude = set(self._active_tasks.keys())

                        asyncio.create_task(self._process_task_async(task, sem))

                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.exception("unexpected_error_in_worker_loop", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

        logger.info("task_worker_loop_ended")

    async def _process_task_async(self, task: Task, sem: asyncio.Semaphore) -> None:
        """Process a task in a thread, bounded by the semaphore."""
        async with sem:
            try:
                await asyncio.to_thread(self._process_task, task)
            finally:
                with self._active_lock:
                    self._active_tasks.pop(task.episode_id, None)

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

                # Chain enqueue next stage if running full pipeline
                self._maybe_enqueue_next_stage(task)

            except FatalError as e:
                # Fatal error - move to DLQ, no retry
                error_msg = str(e)
                logger.error("task_fatal_error", error=error_msg, destination="dlq", exc_info=True)
                self.queue_manager.mark_dead(task.id, error_msg)
                self._mark_episode_failed(task, error_msg, "fatal")
                self._report_failure(task.id, error_msg)

            except TransientError as e:
                # Transient error - schedule retry with backoff
                error_msg = str(e)
                logger.warning("task_transient_error", error=error_msg, will_retry=True, exc_info=True)
                updated_task = self.queue_manager.schedule_retry(task.id, error_msg)
                # Check if retries exhausted (task marked as failed)
                if updated_task and updated_task.status.value == "failed":
                    self._mark_episode_failed(task, error_msg, "transient")
                self._report_failure(task.id, error_msg)

            except Exception as e:
                # Unknown exception - treat as transient and retry
                error_msg = str(e)
                logger.exception("task_unexpected_error", error=error_msg, exc_info=True)
                updated_task = self.queue_manager.schedule_retry(task.id, error_msg)
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

    def _maybe_enqueue_next_stage(self, task: Task) -> None:
        """
        If task has run_full_pipeline metadata, enqueue the next stage.

        Args:
            task: The completed task
        """
        if not task.metadata.get("run_full_pipeline"):
            return

        next_stage = get_next_stage(task.stage)
        if next_stage is None:
            logger.info("pipeline_complete")
            return

        # Check if we've reached the target state
        # Map stage (verb) to resulting episode state (past participle)
        STAGE_TO_STATE = {
            "download": "downloaded",
            "downsample": "downsampled",
            "transcribe": "transcribed",
            "clean": "cleaned",
            "summarize": "summarized",
        }
        target_state = task.metadata.get("target_state", "summarized")
        resulting_state = STAGE_TO_STATE.get(task.stage.value)
        if resulting_state == target_state:
            logger.info("pipeline_target_reached", target_state=target_state)
            return

        # Enqueue next stage with same metadata
        logger.info("chain_enqueueing_next_stage", next_stage=next_stage.value)
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
        """
        Mark the episode as failed when a task reaches final failure.

        Args:
            task: The failed task
            error_msg: Human-readable error message
            failure_type: 'transient' (exhausted retries) or 'fatal' (permanent)
        """
        if not self.repository:
            logger.debug("No repository configured, skipping episode failure tracking")
            return

        try:
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
