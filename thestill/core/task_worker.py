# Copyright 2025 thestill.me
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

The TaskWorker runs in a background thread, polling the queue for pending
tasks and executing them using the appropriate handlers. It handles graceful
shutdown and tracks the currently processing task.

Usage:
    from thestill.core.task_worker import TaskWorker
    from thestill.core.queue_manager import QueueManager, TaskStage
    from thestill.core.task_handlers import create_task_handlers

    queue_manager = QueueManager(db_path)
    handlers = create_task_handlers(app_state)

    worker = TaskWorker(queue_manager, handlers)
    worker.start()

    # ... application runs ...

    worker.stop()  # Graceful shutdown
"""

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Dict, Optional

from .progress import ProgressCallback, ProgressUpdate
from .queue_manager import QueueManager, Task, TaskStage

if TYPE_CHECKING:
    from .progress_store import ProgressStore

logger = logging.getLogger(__name__)


class TaskWorker:
    """
    Background worker that polls the queue and processes tasks.

    Thread-safety: Uses a daemon thread that can be gracefully stopped.
    Handles task execution errors and updates task status accordingly.
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
    ):
        """
        Initialize task worker.

        Args:
            queue_manager: Queue manager for task operations
            task_handlers: Dict mapping TaskStage to handler function
            poll_interval: Seconds between queue polls when idle
            stale_timeout_minutes: Minutes before resetting stale tasks
            progress_store: Optional progress store for real-time progress updates
        """
        self.queue_manager = queue_manager
        self.task_handlers = task_handlers
        self.poll_interval = poll_interval
        self.stale_timeout_minutes = stale_timeout_minutes
        self.progress_store = progress_store

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_task: Optional[Task] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """
        Start the worker thread.

        If the worker is already running, this is a no-op.
        """
        with self._lock:
            if self._running:
                logger.warning("TaskWorker already running")
                return

            self._running = True
            self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="TaskWorker")
            self._thread.start()
            logger.info("TaskWorker started")

    def stop(self, timeout: float = 10.0) -> None:
        """
        Stop the worker thread gracefully.

        Args:
            timeout: Maximum seconds to wait for current task to complete
        """
        with self._lock:
            if not self._running:
                logger.debug("TaskWorker already stopped")
                return

            self._running = False

        if self._thread and self._thread.is_alive():
            logger.info("Waiting for TaskWorker to finish current task...")
            self._thread.join(timeout=timeout)

            if self._thread.is_alive():
                logger.warning("TaskWorker did not stop within timeout")
            else:
                logger.info("TaskWorker stopped")

    def is_running(self) -> bool:
        """Check if worker is running."""
        return self._running and (self._thread is not None and self._thread.is_alive())

    def get_current_task(self) -> Optional[Task]:
        """Get the task currently being processed."""
        return self._current_task

    def get_status(self) -> dict:
        """
        Get worker status information.

        Returns:
            Dictionary with worker status
        """
        return {
            "running": self.is_running(),
            "current_task": self._current_task.to_dict() if self._current_task else None,
            "poll_interval": self.poll_interval,
        }

    def _worker_loop(self) -> None:
        """Main worker loop that polls and processes tasks."""
        logger.info("TaskWorker loop started")

        # Reset any stale tasks from previous runs on startup
        self._reset_stale_tasks()

        while self._running:
            try:
                # Try to get next task
                task = self.queue_manager.get_next_task()

                if task:
                    self._process_task(task)
                else:
                    # No tasks available, sleep before polling again
                    time.sleep(self.poll_interval)

            except Exception as e:
                # Unexpected error in worker loop - log and continue
                logger.exception(f"Unexpected error in TaskWorker loop: {e}")
                time.sleep(self.poll_interval)

        logger.info("TaskWorker loop ended")

    def _process_task(self, task: Task) -> None:
        """
        Process a single task using the appropriate handler.

        Args:
            task: Task to process
        """
        self._current_task = task
        handler = self.task_handlers.get(task.stage)

        if not handler:
            error_msg = f"No handler registered for stage: {task.stage.value}"
            logger.error(error_msg)
            self.queue_manager.fail_task(task.id, error_msg)
            self._current_task = None
            return

        logger.info(f"Processing task {task.id}: {task.stage.value} for episode {task.episode_id}")

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
            logger.info(f"Task {task.id} completed successfully")

        except Exception as e:
            # Handler failed - mark task as failed
            error_msg = str(e)
            logger.exception(f"Task {task.id} failed: {error_msg}")
            self.queue_manager.fail_task(task.id, error_msg)

            # Report failure via progress store
            if self.progress_store:
                from .progress import TranscriptionStage

                self.progress_store.update_from_callback(
                    task.id,
                    ProgressUpdate(
                        stage=TranscriptionStage.FAILED,
                        progress_pct=0,
                        message=f"Task failed: {error_msg}",
                    ),
                )

        finally:
            self._current_task = None
            # Clean up progress store after a delay to allow final updates to be delivered
            if self.progress_store:
                # Schedule cleanup in a separate thread to avoid blocking
                def cleanup():
                    time.sleep(5.0)  # Allow 5 seconds for clients to receive final update
                    self.progress_store.cleanup(task.id)

                threading.Thread(target=cleanup, daemon=True).start()

    def _reset_stale_tasks(self) -> None:
        """Reset any stale processing tasks from previous runs."""
        try:
            reset_count = self.queue_manager.reset_stale_tasks(self.stale_timeout_minutes)
            if reset_count > 0:
                logger.info(f"Reset {reset_count} stale tasks on startup")
        except Exception as e:
            logger.warning(f"Failed to reset stale tasks: {e}")
