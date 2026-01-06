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
In-memory progress store with pub/sub support.

This module provides a thread-safe store for task progress with
async subscription support for SSE streaming.
"""

import asyncio
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .progress import ProgressUpdate, TranscriptionStage

logger = logging.getLogger(__name__)


@dataclass
class TaskProgress:
    """
    Current progress state for a task.

    This is the stored representation of progress, separate from
    ProgressUpdate which is used for callbacks.
    """

    stage: str = TranscriptionStage.PENDING.value
    progress_pct: int = 0
    message: str = ""
    estimated_remaining_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "stage": self.stage,
            "progress_pct": self.progress_pct,
            "message": self.message,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
        }


class ProgressStore:
    """
    Thread-safe in-memory progress store with async pub/sub.

    This store allows the TaskWorker (running in a background thread)
    to update progress, while async SSE handlers can subscribe to
    receive real-time updates.

    Usage:
        store = ProgressStore()

        # From worker thread:
        store.update("task-123", TaskProgress(stage="transcribing", progress_pct=50))

        # From async SSE handler:
        queue = store.subscribe("task-123")
        try:
            while True:
                progress = await queue.get()
                yield f"data: {json.dumps(progress.to_dict())}\\n\\n"
        finally:
            store.unsubscribe("task-123", queue)
    """

    def __init__(self):
        """Initialize the progress store."""
        self._progress: Dict[str, TaskProgress] = {}
        self._subscribers: Dict[str, List[asyncio.Queue]] = defaultdict(list)
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Set the event loop for cross-thread async queue operations.

        This should be called from the main async context (e.g., app startup).
        """
        self._loop = loop

    def update(self, task_id: str, progress: TaskProgress) -> None:
        """
        Update progress for a task and notify subscribers.

        This method is thread-safe and can be called from the worker thread.

        Args:
            task_id: ID of the task
            progress: Current progress state
        """
        with self._lock:
            self._progress[task_id] = progress
            subscribers = list(self._subscribers.get(task_id, []))

        # Notify subscribers outside the lock
        for queue in subscribers:
            try:
                if self._loop and self._loop.is_running():
                    # Schedule the put from worker thread to async loop
                    # Use a helper function to capture queue and progress values
                    def put_to_queue(q: asyncio.Queue[TaskProgress], p: TaskProgress) -> None:
                        try:
                            q.put_nowait(p)
                        except asyncio.QueueFull:
                            pass

                    self._loop.call_soon_threadsafe(put_to_queue, queue, progress)
                else:
                    # Fallback: try direct put (may fail if queue is full)
                    queue.put_nowait(progress)
            except asyncio.QueueFull:
                logger.debug(f"Dropped progress update for task {task_id}: queue full")
            except Exception as e:
                logger.debug(f"Failed to notify subscriber for task {task_id}: {e}")

    def update_from_callback(self, task_id: str, update: ProgressUpdate) -> None:
        """
        Update progress from a ProgressUpdate callback.

        Convenience method that converts ProgressUpdate to TaskProgress.

        Args:
            task_id: ID of the task
            update: Progress update from callback
        """
        progress = TaskProgress(
            stage=update.stage.value,
            progress_pct=update.progress_pct,
            message=update.message,
            estimated_remaining_seconds=update.estimated_remaining_seconds,
        )
        self.update(task_id, progress)

    def get(self, task_id: str) -> Optional[TaskProgress]:
        """
        Get current progress for a task.

        Args:
            task_id: ID of the task

        Returns:
            Current progress or None if not found
        """
        with self._lock:
            return self._progress.get(task_id)

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """
        Subscribe to progress updates for a task.

        Returns an async queue that will receive TaskProgress objects
        whenever progress is updated.

        Args:
            task_id: ID of the task to subscribe to

        Returns:
            Async queue for receiving progress updates
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        with self._lock:
            self._subscribers[task_id].append(queue)

            # Send current progress immediately if available
            current = self._progress.get(task_id)
            if current:
                try:
                    queue.put_nowait(current)
                except asyncio.QueueFull:
                    pass

        logger.debug(f"Subscriber added for task {task_id}")
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue) -> None:
        """
        Unsubscribe from progress updates.

        Args:
            task_id: ID of the task
            queue: Queue returned from subscribe()
        """
        with self._lock:
            if queue in self._subscribers.get(task_id, []):
                self._subscribers[task_id].remove(queue)
        logger.debug(f"Subscriber removed for task {task_id}")

    def cleanup(self, task_id: str) -> None:
        """
        Remove all progress data for a completed task.

        Should be called after task completion and a short delay
        to allow final progress updates to be delivered.

        Args:
            task_id: ID of the task to clean up
        """
        with self._lock:
            self._progress.pop(task_id, None)
            self._subscribers.pop(task_id, None)
        logger.debug(f"Cleaned up progress for task {task_id}")

    def get_all_active(self) -> Dict[str, TaskProgress]:
        """
        Get progress for all active tasks.

        Returns:
            Dictionary mapping task IDs to their progress
        """
        with self._lock:
            return dict(self._progress)
