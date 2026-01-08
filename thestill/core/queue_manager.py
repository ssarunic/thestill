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

import json
import logging
import random
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    RETRY_SCHEDULED = "retry_scheduled"  # Waiting for backoff timer
    FAILED = "failed"  # Exhausted retries (transient errors)
    DEAD = "dead"  # Fatal error, in Dead Letter Queue


class ErrorType(str, Enum):
    """Classification of task errors for retry logic."""

    TRANSIENT = "transient"  # May succeed on retry (network issues, rate limits)
    FATAL = "fatal"  # Will never succeed (404, corrupt file, invalid format)


@dataclass
class Task:
    """Represents a queued task."""

    id: str
    episode_id: str
    stage: TaskStage
    status: TaskStatus
    priority: int = 0
    error_message: Optional[str] = None  # Final error message (when failed/dead)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Retry tracking
    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: Optional[datetime] = None
    error_type: Optional[ErrorType] = None
    last_error: Optional[str] = None  # Most recent error (before final failure)
    # Pipeline metadata (for chain enqueueing)
    metadata: Dict[str, Any] = field(default_factory=dict)

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
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "error_type": self.error_type.value if self.error_type else None,
            "last_error": self.last_error,
            "metadata": self.metadata,
        }


def calculate_backoff(retry_count: int) -> timedelta:
    """
    Calculate exponential backoff delay with jitter.

    Retry schedule:
        - Attempt 0 (first retry): ~5 seconds
        - Attempt 1: ~30 seconds
        - Attempt 2: ~3 minutes
        - Attempt 3+: Give up (caller should check max_retries)

    Args:
        retry_count: Number of retries already attempted (0-based)

    Returns:
        Time to wait before next retry attempt
    """
    base_seconds = 5
    multiplier = 6
    max_seconds = 600  # 10 minute cap

    delay = min(base_seconds * (multiplier**retry_count), max_seconds)
    jitter = random.uniform(0.8, 1.2)  # ±20% jitter to prevent thundering herd

    return timedelta(seconds=delay * jitter)


def get_next_stage(current_stage: TaskStage) -> Optional[TaskStage]:
    """
    Get the next pipeline stage after the current one.

    Args:
        current_stage: The stage that just completed

    Returns:
        Next stage in the pipeline, or None if at final stage
    """
    stage_order = [
        TaskStage.DOWNLOAD,
        TaskStage.DOWNSAMPLE,
        TaskStage.TRANSCRIBE,
        TaskStage.CLEAN,
        TaskStage.SUMMARIZE,
    ]

    try:
        current_index = stage_order.index(current_stage)
        if current_index < len(stage_order) - 1:
            return stage_order[current_index + 1]
    except ValueError:
        pass

    return None


class QueueManager:
    """
    SQLite-based task queue with abstracted interface.

    Thread-safety: Uses per-operation connections with atomic transactions.
    Designed for easy migration to Redis/SQS by maintaining same interface.
    """

    # Default retry configuration
    DEFAULT_MAX_RETRIES = 3

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
        """Create tasks table if not exists and run migrations."""
        with self._get_connection() as conn:
            # Create base table (may already exist from previous versions)
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
                    CHECK (stage IN ('download', 'downsample', 'transcribe', 'clean', 'summarize'))
                )
            """
            )

            # Migration: Add retry tracking columns (idempotent)
            self._migrate_add_column(conn, "tasks", "retry_count", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "tasks", "max_retries", "INTEGER DEFAULT 3")
            self._migrate_add_column(conn, "tasks", "next_retry_at", "TIMESTAMP NULL")
            self._migrate_add_column(conn, "tasks", "error_type", "TEXT NULL")
            self._migrate_add_column(conn, "tasks", "last_error", "TEXT NULL")
            self._migrate_add_column(conn, "tasks", "metadata", "TEXT NULL")

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
                WHERE status IN ('pending', 'processing', 'retry_scheduled')
            """
            )
            # Index for finding tasks ready for retry
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_retry_scheduled
                ON tasks(next_retry_at)
                WHERE status = 'retry_scheduled'
            """
            )

            logger.debug("Tasks table ensured")

    def _migrate_add_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        """Add a column to a table if it doesn't exist (idempotent migration)."""
        cursor = conn.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            logger.info(f"Migration: Added column {column} to {table}")

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert database row to Task object."""
        # Parse metadata JSON if present
        metadata = {}
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Failed to parse task metadata for task {row['id']}")

        # Parse error_type enum if present
        error_type = None
        if row["error_type"]:
            try:
                error_type = ErrorType(row["error_type"])
            except ValueError:
                logger.warning(f"Unknown error_type '{row['error_type']}' for task {row['id']}")

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
            retry_count=row["retry_count"] or 0,
            max_retries=row["max_retries"] or self.DEFAULT_MAX_RETRIES,
            next_retry_at=(datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None),
            error_type=error_type,
            last_error=row["last_error"],
            metadata=metadata,
        )

    def add_task(
        self,
        episode_id: str,
        stage: TaskStage,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: Optional[int] = None,
    ) -> Task:
        """
        Add a new task to the queue.

        Args:
            episode_id: ID of the episode to process
            stage: Pipeline stage to execute
            priority: Higher priority tasks are processed first (default: 0)
            metadata: Optional metadata dict (e.g., {"run_full_pipeline": True})
            max_retries: Override default max retries for this task

        Returns:
            The created Task object
        """
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        metadata = metadata or {}
        max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        metadata_json = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, episode_id, stage, status, priority, max_retries, metadata, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
                (task_id, episode_id, stage.value, priority, max_retries, metadata_json, now, now),
            )

        logger.info(f"Task queued: {task_id} - {stage.value} for episode {episode_id}")

        return Task(
            id=task_id,
            episode_id=episode_id,
            stage=stage,
            status=TaskStatus.PENDING,
            priority=priority,
            max_retries=max_retries,
            metadata=metadata,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def get_next_task(self, stage: Optional[TaskStage] = None) -> Optional[Task]:
        """
        Get the next pending or ready-to-retry task, atomically marking it as 'processing'.

        This considers both:
        - Tasks with status='pending'
        - Tasks with status='retry_scheduled' where next_retry_at <= now

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
                # Find next task that's either pending OR retry_scheduled and ready
                # Order: priority DESC, then created_at ASC
                if stage:
                    cursor = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE stage = ? AND (
                            status = 'pending'
                            OR (status = 'retry_scheduled' AND next_retry_at <= ?)
                        )
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                    """,
                        (stage.value, now),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT * FROM tasks
                        WHERE status = 'pending'
                           OR (status = 'retry_scheduled' AND next_retry_at <= ?)
                        ORDER BY priority DESC, created_at ASC
                        LIMIT 1
                    """,
                        (now,),
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

                logger.info(f"Task claimed: {task_id} - {task.stage.value} (retry #{task.retry_count})")
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
        Check if an episode already has a pending, processing, or retry_scheduled task for a stage.

        Args:
            episode_id: ID of the episode
            stage: Pipeline stage to check

        Returns:
            True if there's an active task, False otherwise
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE episode_id = ? AND stage = ? AND status IN ('pending', 'processing', 'retry_scheduled')
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
        Delete completed/failed/dead tasks older than specified days.

        Args:
            days: Delete tasks older than this many days

        Returns:
            Number of tasks deleted
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'failed', 'dead')
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

    def schedule_retry(self, task_id: str, error_message: str) -> Optional[Task]:
        """
        Schedule a task for retry with exponential backoff.

        If the task has exceeded max_retries, it will be marked as FAILED instead.

        Args:
            task_id: ID of the task to retry
            error_message: The error that caused the failure

        Returns:
            Updated Task object, or None if task not found
        """
        now = datetime.utcnow()
        now_iso = now.isoformat()

        with self._get_connection() as conn:
            # Get current task state
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Cannot schedule retry for unknown task: {task_id}")
                return None

            current_retry = row["retry_count"] or 0
            max_retries = row["max_retries"] or self.DEFAULT_MAX_RETRIES
            new_retry_count = current_retry + 1

            if new_retry_count >= max_retries:
                # Exhausted retries - mark as failed
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        retry_count = ?,
                        error_message = ?,
                        last_error = ?,
                        error_type = 'transient',
                        completed_at = ?,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (new_retry_count, error_message, error_message, now_iso, now_iso, task_id),
                )
                logger.warning(f"Task {task_id} exhausted retries ({new_retry_count}/{max_retries}), marked as failed")
            else:
                # Schedule retry with exponential backoff
                backoff = calculate_backoff(new_retry_count)
                next_retry = now + backoff
                next_retry_iso = next_retry.isoformat()

                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'retry_scheduled',
                        retry_count = ?,
                        next_retry_at = ?,
                        last_error = ?,
                        error_type = 'transient',
                        started_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (new_retry_count, next_retry_iso, error_message, now_iso, task_id),
                )
                logger.info(
                    f"Task {task_id} scheduled for retry {new_retry_count}/{max_retries} "
                    f"at {next_retry_iso} (in {backoff.total_seconds():.0f}s)"
                )

            # Return updated task
            return self.get_task(task_id)

    def mark_dead(self, task_id: str, error_message: str) -> Optional[Task]:
        """
        Move a task to the Dead Letter Queue (status='dead').

        Use this for fatal errors that will never succeed on retry.

        Args:
            task_id: ID of the task to mark as dead
            error_message: The fatal error description

        Returns:
            Updated Task object, or None if task not found
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'dead',
                    error_message = ?,
                    error_type = 'fatal',
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """,
                (error_message, now, now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot mark unknown task as dead: {task_id}")
                return None

        logger.error(f"Task {task_id} moved to DLQ (dead): {error_message}")
        return self.get_task(task_id)

    def get_dead_tasks(self, limit: int = 100) -> List[Task]:
        """
        Get tasks in the Dead Letter Queue.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of dead tasks, ordered by most recent first
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'dead'
                ORDER BY completed_at DESC
                LIMIT ?
            """,
                (limit,),
            )

            return [self._row_to_task(row) for row in cursor.fetchall()]

    def retry_dead_task(self, task_id: str) -> Optional[Task]:
        """
        Move a dead task back to pending for manual retry.

        Args:
            task_id: ID of the dead task to retry

        Returns:
            Updated Task object, or None if task not found or not dead
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'pending',
                    retry_count = 0,
                    error_message = NULL,
                    error_type = NULL,
                    last_error = NULL,
                    next_retry_at = NULL,
                    started_at = NULL,
                    completed_at = NULL,
                    updated_at = ?
                WHERE id = ? AND status = 'dead'
            """,
                (now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot retry task {task_id}: not found or not dead")
                return None

        logger.info(f"Task {task_id} moved from DLQ back to pending")
        return self.get_task(task_id)

    def cancel_retry(self, task_id: str) -> Optional[Task]:
        """
        Cancel a scheduled retry, marking the task as failed.

        Args:
            task_id: ID of the task to cancel

        Returns:
            Updated Task object, or None if task not found or not in retry_scheduled status
        """
        now = datetime.utcnow().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = COALESCE(last_error, 'Retry cancelled by user'),
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'retry_scheduled'
            """,
                (now, now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot cancel retry for task {task_id}: not found or not scheduled")
                return None

        logger.info(f"Task {task_id} retry cancelled")
        return self.get_task(task_id)
