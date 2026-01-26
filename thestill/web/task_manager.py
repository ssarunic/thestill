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
Task manager for preventing concurrent long-running operations.

This module provides a thread-safe task manager that ensures only one
instance of a given task type can run at a time. It tracks task status,
progress, and results.

Usage:
    from thestill.web.task_manager import TaskManager, TaskType

    task_manager = TaskManager()

    # Try to start a task
    task = task_manager.start_task(TaskType.REFRESH)
    if task is None:
        # Task already running
        current = task_manager.get_task(TaskType.REFRESH)
        raise HTTPException(409, f"Task already running since {current.started_at}")

    try:
        # Do work...
        task_manager.update_progress(TaskType.REFRESH, 50, "Processing...")
        # More work...
        task_manager.complete_task(TaskType.REFRESH, result={"episodes": 10})
    except Exception as e:
        task_manager.fail_task(TaskType.REFRESH, str(e))
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from structlog import get_logger

logger = get_logger(__name__)


class TaskType(str, Enum):
    """Types of long-running tasks that can be managed."""

    REFRESH = "refresh"
    DOWNLOAD = "download"
    DOWNSAMPLE = "downsample"
    TRANSCRIBE = "transcribe"
    CLEAN = "clean"
    SUMMARIZE = "summarize"
    ADD_PODCAST = "add_podcast"


class TaskStatus(str, Enum):
    """Status of a managed task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Represents a managed task with status and progress tracking."""

    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress: int = 0  # 0-100
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary for JSON serialization."""
        return {
            "task_type": self.task_type.value,
            "status": self.status.value,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class TaskManager:
    """
    Thread-safe manager for long-running tasks.

    Ensures only one instance of each task type can run concurrently.
    Tracks task status, progress, and results for API queries.

    Attributes:
        _tasks: Dictionary mapping task types to their current/last task
        _lock: Threading lock for thread-safe operations
    """

    _tasks: Dict[TaskType, Task] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start_task(self, task_type: TaskType, message: str = "") -> Optional[Task]:
        """
        Attempt to start a new task of the given type.

        Args:
            task_type: Type of task to start
            message: Optional initial status message

        Returns:
            Task object if started successfully, None if task already running
        """
        with self._lock:
            existing = self._tasks.get(task_type)
            if existing and existing.status == TaskStatus.RUNNING:
                logger.warning(f"Task {task_type.value} already running since {existing.started_at}")
                return None

            task = Task(
                task_type=task_type,
                status=TaskStatus.RUNNING,
                started_at=datetime.utcnow(),
                message=message or f"Starting {task_type.value}...",
            )
            self._tasks[task_type] = task
            logger.info(f"Started task: {task_type.value}")
            return task

    def update_progress(self, task_type: TaskType, progress: int, message: str = "") -> None:
        """
        Update the progress of a running task.

        Args:
            task_type: Type of task to update
            progress: Progress percentage (0-100)
            message: Optional status message
        """
        with self._lock:
            task = self._tasks.get(task_type)
            if task and task.status == TaskStatus.RUNNING:
                task.progress = min(100, max(0, progress))
                if message:
                    task.message = message
                logger.debug(f"Task {task_type.value} progress: {progress}% - {message}")

    def complete_task(self, task_type: TaskType, result: Optional[Dict[str, Any]] = None, message: str = "") -> None:
        """
        Mark a task as completed successfully.

        Args:
            task_type: Type of task to complete
            result: Optional result data
            message: Optional completion message
        """
        with self._lock:
            task = self._tasks.get(task_type)
            if task:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.utcnow()
                task.progress = 100
                task.result = result
                task.message = message or f"{task_type.value} completed successfully"
                logger.info(f"Completed task: {task_type.value}")

    def fail_task(self, task_type: TaskType, error: str) -> None:
        """
        Mark a task as failed.

        Args:
            task_type: Type of task that failed
            error: Error message describing the failure
        """
        with self._lock:
            task = self._tasks.get(task_type)
            if task:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.utcnow()
                task.error = error
                task.message = f"{task_type.value} failed: {error}"
                logger.error(f"Failed task: {task_type.value} - {error}")

    def get_task(self, task_type: TaskType) -> Optional[Task]:
        """
        Get the current or last task of the given type.

        Args:
            task_type: Type of task to retrieve

        Returns:
            Task object if exists, None otherwise
        """
        with self._lock:
            return self._tasks.get(task_type)

    def is_running(self, task_type: TaskType) -> bool:
        """
        Check if a task of the given type is currently running.

        Args:
            task_type: Type of task to check

        Returns:
            True if task is running, False otherwise
        """
        with self._lock:
            task = self._tasks.get(task_type)
            return task is not None and task.status == TaskStatus.RUNNING

    def get_all_tasks(self) -> Dict[str, Dict[str, Any]]:
        """
        Get status of all tracked tasks.

        Returns:
            Dictionary mapping task type names to their status dictionaries
        """
        with self._lock:
            return {task_type.value: task.to_dict() for task_type, task in self._tasks.items()}


# Global singleton instance for the web server
_task_manager: Optional[TaskManager] = None
_manager_lock = threading.Lock()


def get_task_manager() -> TaskManager:
    """
    Get the global TaskManager singleton.

    Returns:
        Global TaskManager instance
    """
    global _task_manager
    with _manager_lock:
        if _task_manager is None:
            _task_manager = TaskManager()
        return _task_manager
