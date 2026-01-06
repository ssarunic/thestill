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
SQLite-based task queue for background processing.

This module provides a task queue abstraction backed by SQLite for reliable
background processing of pipeline operations. The interface is designed to
allow easy migration to distributed queues (Redis, SQS) in the future.

Usage:
    from thestill.core.queue_manager import QueueManager, TaskStage

    queue = QueueManager(db_path="./data/podcasts.db")

    # Add a task
    task = queue.add_task(episode_id="abc-123", stage=TaskStage.DOWNLOAD)

    # Worker picks up next task (atomically marks as processing)
    task = queue.get_next_task()
    if task:
        try:
            # Do work...
            queue.complete_task(task.id)
        except Exception as e:
            queue.fail_task(task.id, str(e))
"""

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class TaskStage(str, Enum):
    """Pipeline stages that can be queued."""

    DOWNLOAD = "download"
    DOWNSAMPLE = "downsample"
    TRANSCRIBE = "transcribe"
    CLEAN = "clean"
    SUMMARIZE = "summarize"


class TaskStatus(str, Enum):
    """Status of a queued task."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Represents a queued task."""

    id: str
    episode_id: str
    stage: TaskStage
    status: TaskStatus
    priority: int = 0
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert task to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "stage": self.stage.value,
            "status": self.status.value,
            "priority": self.priority,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class QueueManager:
    """
    SQLite-based task queue with abstracted interface.

    Thread-safety: Uses per-operation connections with atomic transactions.
    Designed for easy migration to Redis/SQS by maintaining same interface.
    """

    def __init__(self, db_path: str):
        """
        Initialize queue manager.

        Args:
            db_path: Path to SQLite database file (shared with podcast repository)
        """
        self.db_path = Path(db_path)
        self._ensure_table()
        logger.info(f"QueueManager initialized: {self.db_path}")

    @contextmanager
    def _get_connection(self):
        """Get database connection with proper setup."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_table(self):
        """Create tasks table if not exists."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY NOT NULL,
                    episode_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    error_message TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP NULL,
                    completed_at TIMESTAMP NULL,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id),
                    CHECK (length(id) = 36),
                    CHECK (stage IN ('download', 'downsample', 'transcribe', 'clean', 'summarize')),
                    CHECK (status IN ('pending', 'processing', 'completed', 'failed'))
                )
            """
            )

            # Indexes for efficient querying
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
                ON tasks(status, priority DESC, created_at ASC)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_episode_id
                ON tasks(episode_id)
            """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_episode_stage_pending
                ON tasks(episode_id, stage)
                WHERE status IN ('pending', 'processing')
            """
            )

            logger.debug("Tasks table ensured")

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert database row to Task object."""
        return Task(
            id=row["id"],
            episode_id=row["episode_id"],
            stage=TaskStage(row["stage"]),
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
        )

    def add_task(self, episode_id: str, stage: TaskStage, priority: int = 0) -> Task:
        """
        Add a new task to the queue.

        Args:
            episode_id: ID of the episode to process
            stage: Pipeline stage to execute
            priority: Higher priority tasks are processed first (default: 0)

        Returns:
            The created Task object
        """
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, episode_id, stage, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
                (task_id, episode_id, stage.value, priority, now, now),
            )

        logger.info(f"Task queued: {task_id} - {stage.value} for episode {episode_id}")

        return Task(
            id=task_id,
            episode_id=episode_id,
            stage=stage,
            status=TaskStatus.PENDING,
            priority=priority,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_next_task(self, stage: Optional[TaskStage] = None) -> Optional[Task]:
        """
        Get the next pending task, atomically marking it as 'processing'.

        This uses SQLite's row locking via UPDATE...RETURNING to prevent
        race conditions when multiple workers poll simultaneously.

        Args:
            stage: Optionally filter to a specific stage

        Returns:
            Task if one is available, None otherwise
        """
        with self._get_connection() as conn:
            now = datetime.utcnow().isoformat()

            # SQLite doesn't have UPDATE...RETURNING, so we use a transaction
            # with immediate locking to prevent race conditions
            conn.execute("BEGIN IMMEDIATE")

            try:
                # Find next pending task (ordered by priority DESC, created_at ASC)
                if stage:
                    cursor = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE status = 'pending' AND stage = ?
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                    """,
                        (stage.value,),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE status = 'pending'
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                    """
                    )

                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return None

                task_id = row["id"]

                # Atomically mark as processing
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'processing', started_at = ?, updated_at = ?
                    WHERE id = ?
                """,
                    (now, now, task_id),
                )

                conn.commit()

                # Return the task with updated status
                task = self._row_to_task(row)
                task.status = TaskStatus.PROCESSING
                task.started_at = datetime.fromisoformat(now)
                task.updated_at = datetime.fromisoformat(now)

                logger.info(f"Task claimed: {task_id} - {task.stage.value}")
                return task

            except Exception:
                conn.rollback()
                raise

    def complete_task(self, task_id: str) -> None:
        """
        Mark a task as completed.

        Args:
            task_id: ID of the task to complete
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ?
            """,
                (now, now, task_id),
            )

        logger.info(f"Task completed: {task_id}")

    def fail_task(self, task_id: str, error_message: str) -> None:
        """
        Mark a task as failed.

        Args:
            task_id: ID of the task that failed
            error_message: Error description
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed', error_message = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
            """,
                (error_message, now, now, task_id),
            )

        logger.error(f"Task failed: {task_id} - {error_message}")

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        Get a task by ID.

        Args:
            task_id: ID of the task to retrieve

        Returns:
            Task if found, None otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return self._row_to_task(row)

    def get_tasks_for_episode(self, episode_id: str) -> List[Task]:
        """
        Get all tasks for an episode.

        Args:
            episode_id: ID of the episode

        Returns:
            List of tasks for the episode
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE episode_id = ?
                ORDER BY created_at DESC
            """,
                (episode_id,),
            )

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def has_pending_task(self, episode_id: str, stage: TaskStage) -> bool:
        """
        Check if an episode already has a pending or processing task for a stage.

        Args:
            episode_id: ID of the episode
            stage: Pipeline stage to check

        Returns:
            True if there's a pending/processing task, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE episode_id = ? AND stage = ? AND status IN ('pending', 'processing')
                LIMIT 1
            """,
                (episode_id, stage.value),
            )

            return cursor.fetchone() is not None

    def get_pending_count(self) -> int:
        """
        Get the count of pending tasks.

        Returns:
            Number of pending tasks
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) as count FROM tasks WHERE status = 'pending'")
            row = cursor.fetchone()
            return row["count"] if row else 0

    def get_queue_stats(self) -> dict:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue stats
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    status,
                    COUNT(*) as count
                FROM tasks
                GROUP BY status
            """
            )

            stats = {status.value: 0 for status in TaskStatus}
            for row in cursor.fetchall():
                stats[row["status"]] = row["count"]

            return stats

    def cleanup_old_tasks(self, days: int = 7) -> int:
        """
        Delete completed/failed tasks older than specified days.

        Args:
            days: Delete tasks older than this many days

        Returns:
            Number of tasks deleted
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'failed')
                AND completed_at < datetime('now', '-' || ? || ' days')
            """,
                (days,),
            )

            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old tasks")

            return deleted

    def reset_stale_tasks(self, timeout_minutes: int = 30) -> int:
        """
        Reset tasks that have been processing for too long back to pending.

        This handles cases where a worker crashed while processing a task.

        Args:
            timeout_minutes: Consider tasks stale after this many minutes

        Returns:
            Number of tasks reset
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', started_at = NULL, updated_at = ?
                WHERE status = 'processing'
                AND started_at < datetime('now', '-' || ? || ' minutes')
            """,
                (now, timeout_minutes),
            )

            reset = cursor.rowcount
            if reset > 0:
                logger.warning(f"Reset {reset} stale tasks back to pending")

            return reset

    def recover_interrupted_tasks(self, excluded_stages: Optional[List[TaskStage]] = None) -> int:
        """
        Mark tasks left in 'processing' status as failed.

        This should be called on server startup to handle tasks that were
        interrupted by a server restart or crash. Unlike reset_stale_tasks,
        this marks them as failed rather than pending, since:
        1. The work may have partially completed (e.g., partial transcription)
        2. It's safer to let the user manually retry than auto-retry
        3. It provides visibility that something went wrong

        Args:
            excluded_stages: Stages to NOT recover (e.g., cloud tasks that may
                           still be running remotely). Tasks in these stages
                           will be left in 'processing' status.

        Returns:
            Number of tasks marked as failed
        """
        now = datetime.utcnow().isoformat()
        excluded_stages = excluded_stages or []

        with self._get_connection() as conn:
            if excluded_stages:
                # Build placeholders for excluded stages
                placeholders = ",".join("?" * len(excluded_stages))
                excluded_values = [s.value for s in excluded_stages]

                cursor = conn.execute(
                    f"""
                    UPDATE tasks
                    SET status = 'failed',
                        error_message = 'Task interrupted by server restart',
                        completed_at = ?,
                        updated_at = ?
                    WHERE status = 'processing'
                    AND stage NOT IN ({placeholders})
                """,
                    (now, now, *excluded_values),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        error_message = 'Task interrupted by server restart',
                        completed_at = ?,
                        updated_at = ?
                    WHERE status = 'processing'
                """,
                    (now, now),
                )

            count = cursor.rowcount
            if count > 0:
                logger.warning(f"Recovered {count} interrupted task(s) - marked as failed")

            return count
