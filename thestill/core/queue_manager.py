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
import random
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from structlog import get_logger

from ..utils.datetime_utils import now_utc

logger = get_logger(__name__)


class TaskStage(str, Enum):
    """Pipeline stages that can be queued.

    The four ``*_ENTITIES`` / corpus stages (spec #28) form an
    asynchronous branch off ``CLEAN`` that runs in parallel with
    ``SUMMARIZE`` — see ``STAGE_SUCCESSORS`` below for the dependency
    graph and ``_NON_USER_FAILING_STAGES`` for the failure-isolation
    contract.
    """

    DOWNLOAD = "download"
    DOWNSAMPLE = "downsample"
    TRANSCRIBE = "transcribe"
    CLEAN = "clean"
    SUMMARIZE = "summarize"
    EXTRACT_ENTITIES = "extract-entities"
    RESOLVE_ENTITIES = "resolve-entities"
    REINDEX = "reindex"
    REBUILD_COOCCURRENCES = "rebuild-cooccurrences"
    # Spec #46 — terminal entity-branch stage; refreshes the precomputed
    # "Related episodes" rail for the just-indexed episodes (+ their
    # neighbours). Coalesced + corpus-global like REBUILD_COOCCURRENCES.
    COMPUTE_RELATED = "compute-related"
    # Spec #47 — terminal entity-branch stage; fetches Wikidata/Wikipedia
    # display data (photo, headline, bio, vital stats) for entities
    # mentioned in the just-processed episodes. Network-bound and coalesced
    # like the corpus stages, but iterates per-entity. Runs LAST so its
    # latency never delays REINDEX/COMPUTE_RELATED (search + related rail).
    # The scheduled ``enrich-entities`` batch still owns retries + staleness.
    ENRICH_ENTITIES = "enrich-entities"

    # Spec #48 — podcast-scoped root/producer stage. One task = one feed.
    # Carries ``podcast_id`` (not ``episode_id``), fans out DOWNLOAD tasks for
    # newly-discovered episodes, and is a separate (feed-scoped) failure
    # domain — see ``_FEED_SCOPED_STAGES`` / ``is_feed_scoped_stage``.
    REFRESH_FEED = "refresh-feed"


# Spec #48 §"Failure isolation" — REFRESH_FEED is podcast-scoped: it has no
# episode to mark failed. A failure writes podcast-level state
# (``podcasts.last_refresh_error``) instead of ``episodes.failed_at_stage``,
# and a terminal failure parks the feed (``next_refresh_at = NULL``).
# ``task_worker._mark_episode_failed`` branches on this set.
_FEED_SCOPED_STAGES = frozenset({TaskStage.REFRESH_FEED})


def is_feed_scoped_stage(stage: TaskStage) -> bool:
    """Return True if ``stage`` targets a podcast (``podcast_id``) rather than
    an episode (``episode_id``) — currently only REFRESH_FEED (spec #48)."""
    return stage in _FEED_SCOPED_STAGES


# Spec #49 Layer 4 — stages that re-run deterministically from their inputs and
# are therefore safe to AUTO-RESUME (reset to 'pending') after an interruption,
# rather than being marked 'failed' for a human to retry. The user chain
# (download→summarize) each reads a durable upstream artifact and rewrites its
# own output idempotently; REFRESH_FEED is the spec #48 precedent (re-fetching a
# feed is safe). Entity-branch stages are deliberately EXCLUDED — they are a
# separate, coalesced corpus-mutation domain whose restart semantics are out of
# this carve-out's scope, so they keep the conservative →failed behaviour.
_IDEMPOTENT_STAGES = frozenset(
    {
        TaskStage.REFRESH_FEED,
        TaskStage.DOWNLOAD,
        TaskStage.DOWNSAMPLE,
        TaskStage.TRANSCRIBE,
        TaskStage.CLEAN,
        TaskStage.SUMMARIZE,
    }
)


def is_idempotent_stage(stage: TaskStage) -> bool:
    """Return True if ``stage`` is safe to auto-resume after an interruption.

    Drives ``recover_interrupted_tasks`` (spec #49 L4): an interrupted
    idempotent stage is reset to ``pending`` (resume) instead of ``failed``,
    so a server restart mid-pipeline doesn't strand re-runnable work in the
    DLQ. Generalises the spec #48 feed-scoped carve-out to the whole user chain.
    """
    return stage in _IDEMPOTENT_STAGES


# Spec #28 §6 — the entity branch is a separate failure domain. A
# failure on any of these stages must not mark the episode as failed in
# the user-facing sense (``episodes.failed_at_stage``); only its
# ``entity_extraction_status`` is bumped. ``task_worker._mark_episode_failed``
# branches on this set.
_NON_USER_FAILING_STAGES = frozenset(
    {
        TaskStage.EXTRACT_ENTITIES,
        TaskStage.RESOLVE_ENTITIES,
        TaskStage.REINDEX,
        TaskStage.REBUILD_COOCCURRENCES,
        TaskStage.COMPUTE_RELATED,
        TaskStage.ENRICH_ENTITIES,
    }
)


def is_entity_branch_stage(stage: TaskStage) -> bool:
    """Return True if ``stage`` belongs to the spec #28 entity branch.

    Used by the worker to decide whether a failure should propagate to
    ``episodes.failed_at_stage`` (user-facing) or only to
    ``episodes.entity_extraction_status`` (entity-only).
    """
    return stage in _NON_USER_FAILING_STAGES


# Public alias for callers building stage filters (e.g. the DLQ
# entity-branch filter in spec #28 Phase 3.2). The frozenset above is
# private because its membership is tied to failure-isolation semantics;
# this constant is the same set under a name that conveys "branch".
ENTITY_BRANCH_STAGES: frozenset[TaskStage] = _NON_USER_FAILING_STAGES


# Spec #28 §0.5 — fully linear chain. The entity branch was originally
# designed to fan out from CLEAN in parallel with SUMMARIZE, but the
# worker's per-episode mutex serialised them anyway and the spec design
# evolved to want SUMMARIZE durable on disk before entity extraction
# runs (so a future GLiNER variant can feed off summary text). Keeping
# the chain literally linear makes the dependency explicit, leaves the
# failure-isolation rule for entity stages unchanged, and matches what
# users see in the queue viewer (one stage at a time per episode).
STAGE_SUCCESSORS: Dict[TaskStage, List[TaskStage]] = {
    TaskStage.DOWNLOAD: [TaskStage.DOWNSAMPLE],
    TaskStage.DOWNSAMPLE: [TaskStage.TRANSCRIBE],
    TaskStage.TRANSCRIBE: [TaskStage.CLEAN],
    TaskStage.CLEAN: [TaskStage.SUMMARIZE],
    TaskStage.SUMMARIZE: [TaskStage.EXTRACT_ENTITIES],
    TaskStage.EXTRACT_ENTITIES: [TaskStage.RESOLVE_ENTITIES],
    TaskStage.RESOLVE_ENTITIES: [TaskStage.REINDEX],
    TaskStage.REINDEX: [TaskStage.REBUILD_COOCCURRENCES],
    TaskStage.REBUILD_COOCCURRENCES: [TaskStage.COMPUTE_RELATED],
    TaskStage.COMPUTE_RELATED: [TaskStage.ENRICH_ENTITIES],
    TaskStage.ENRICH_ENTITIES: [],
    # Spec #48 — REFRESH_FEED is a root/producer with no STATIC successor: it
    # fans out DOWNLOAD per newly-discovered episode dynamically inside the
    # handler (data-driven, not one→one). Mapped to ``[]`` to satisfy the
    # "every stage has an entry" invariant; the worker never auto-chains a
    # feed task (it has no episode), so this empty list is the correct no-op.
    TaskStage.REFRESH_FEED: [],
}


def get_next_stages(current_stage: TaskStage) -> List[TaskStage]:
    """Return the stages that should be enqueued after ``current_stage``.

    A list (not an Optional) so the same primitive could later support
    fan-out if the design ever needs it; today every entry is at most
    one successor. Empty list means the chain terminates.
    """
    return list(STAGE_SUCCESSORS.get(current_stage, []))


# Per-branch stage ordering, used by ``supersede_stale_tasks`` to decide
# which DLQ rows for an episode have been made obsolete by a later stage
# succeeding. The user chain and the entity branch are separate failure
# domains (see ``_NON_USER_FAILING_STAGES``); a successful ``summarize``
# does not supersede a dead ``extract-entities`` and vice versa.
_USER_CHAIN_ORDER: List[TaskStage] = [
    TaskStage.DOWNLOAD,
    TaskStage.DOWNSAMPLE,
    TaskStage.TRANSCRIBE,
    TaskStage.CLEAN,
    TaskStage.SUMMARIZE,
]
_ENTITY_BRANCH_ORDER: List[TaskStage] = [
    TaskStage.EXTRACT_ENTITIES,
    TaskStage.RESOLVE_ENTITIES,
    TaskStage.REINDEX,
    TaskStage.REBUILD_COOCCURRENCES,
    TaskStage.COMPUTE_RELATED,
    TaskStage.ENRICH_ENTITIES,
]


def _stages_at_or_before(stage: TaskStage) -> List[TaskStage]:
    """Stages in the same branch up to and including ``stage``.

    Used to find DLQ rows that a successful ``stage`` makes moot.
    """
    for branch in (_USER_CHAIN_ORDER, _ENTITY_BRANCH_ORDER):
        if stage in branch:
            idx = branch.index(stage)
            return branch[: idx + 1]
    return [stage]


class TaskStatus(str, Enum):
    """Status of a queued task."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRY_SCHEDULED = "retry_scheduled"  # Waiting for backoff timer
    FAILED = "failed"  # Exhausted retries (transient errors)
    DEAD = "dead"  # Fatal error, in Dead Letter Queue
    # Terminal: a previously dead/failed row whose stage has since been
    # made moot by the same episode reaching a later stage through a
    # different run (e.g. user fixed the API key and re-triggered the
    # pipeline). The DLQ default view hides these so stale rows don't
    # train users to ignore the queue. See ``supersede_stale_tasks``.
    SUPERSEDED = "superseded"


class ErrorType(str, Enum):
    """Classification of task errors for retry logic."""

    TRANSIENT = "transient"  # May succeed on retry (network issues, rate limits)
    FATAL = "fatal"  # Will never succeed (404, corrupt file, invalid format)


@dataclass
class Task:
    """Represents a queued task."""

    id: str
    # Exactly one of ``episode_id`` / ``podcast_id`` is set (enforced by a
    # table CHECK). Episode-scoped stages (DOWNLOAD…ENRICH_ENTITIES) carry
    # ``episode_id``; the podcast-scoped REFRESH_FEED stage (spec #48) carries
    # ``podcast_id`` and has no episode at enqueue time — it produces them.
    episode_id: Optional[str] = None
    podcast_id: Optional[str] = None
    stage: TaskStage = TaskStage.DOWNLOAD
    status: TaskStatus = TaskStatus.PENDING
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
    # Spec #49 — auto-healing attribution. ``error_class`` is 'infra' | 'item'
    # | 'fatal' (None on legacy rows); the healer only requeues 'infra'.
    # ``heal_attempts`` bounds that second look; ``last_heal_at`` gates cooldown.
    error_class: Optional[str] = None
    heal_attempts: int = 0
    last_heal_at: Optional[datetime] = None
    # Pipeline metadata (for chain enqueueing)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert task to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "episode_id": self.episode_id,
            "podcast_id": self.podcast_id,
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
            "error_class": self.error_class,
            "heal_attempts": self.heal_attempts,
            "last_heal_at": self.last_heal_at.isoformat() if self.last_heal_at else None,
            "metadata": self.metadata,
        }


_R = TypeVar("_R")

# Backoff schedule for retrying SQLite write attempts that lose the 5s
# ``busy_timeout`` race against concurrent writers (reindex/cooccurrence
# stages can hold the WAL writer for a few seconds at a time). Each entry
# is the sleep BEFORE the next attempt; the final attempt has no follow-up.
_LOCK_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.3, 0.7, 1.5, 3.0)


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

    def _exec_with_lock_retry(self, op_name: str, fn: Callable[[], _R]) -> _R:
        """Retry an idempotent SQLite write on transient ``database is locked``.

        The task handler's actual work (transcript files, facts, etc.) is on
        disk by the time bookkeeping writes like ``complete_task`` and
        ``schedule_retry`` run. Losing one of those one-row UPDATEs to a
        lock race strands the task in ``processing`` forever, so we pay a
        short backoff to get the write to land. Non-lock errors propagate
        immediately.
        """
        for attempt, delay in enumerate(_LOCK_RETRY_DELAYS):
            try:
                return fn()
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower():
                    raise
                logger.warning(
                    "queue_write_lock_retry",
                    op=op_name,
                    attempt=attempt + 1,
                    sleep_s=delay,
                    error=str(e),
                )
                time.sleep(delay)
        # Final attempt; whatever it raises is the caller's problem.
        return fn()

    @contextmanager
    def _get_connection(self):
        """Get database connection with proper setup.

        Spec #25 item 3.6 — concurrency hardening for the task queue:
        - ``journal_mode=WAL`` lets readers and one writer proceed
          concurrently instead of stalling everyone behind a single
          rollback-journal write lock. WAL is set per-DB persistently
          on first use.
        - ``busy_timeout=5000`` (ms) makes a contended ``BEGIN IMMEDIATE``
          wait up to 5s for the other writer to commit, instead of
          immediately failing with ``database is locked``. With multiple
          workers competing for the next task this is the difference
          between "graceful serialisation" and "spurious crashes".
        """
        from ..utils.sqlite_ext import maybe_load_vec_extension

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        maybe_load_vec_extension(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Spec #28 §0.5 — the canonical CHECK clause for ``tasks.stage``.
    # Derived from ``TaskStage`` so adding a stage in the enum auto-updates
    # both the fresh-DB ``CREATE TABLE`` and the rebuild migration's DDL.
    _TASKS_STAGE_CHECK = "CHECK (stage IN ({values}))".format(values=", ".join(f"'{s.value}'" for s in TaskStage))

    # Spec #48 — exactly one of (episode_id, podcast_id) is set. ``IS NOT NULL``
    # yields 1/0; ``<>`` is XOR, so the row is valid iff precisely one target is
    # present. Rejects rows with both set or neither set.
    _TASKS_TARGET_CHECK = "CHECK ((episode_id IS NOT NULL) <> (podcast_id IS NOT NULL))"

    def _ensure_table(self):
        """Create tasks table if not exists and run migrations."""
        with self._get_connection() as conn:
            # Create base table (may already exist from previous versions)
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY NOT NULL,
                    episode_id TEXT NULL,
                    podcast_id TEXT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    error_message TEXT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP NULL,
                    completed_at TIMESTAMP NULL,
                    FOREIGN KEY (episode_id) REFERENCES episodes(id),
                    FOREIGN KEY (podcast_id) REFERENCES podcasts(id),
                    CHECK (length(id) = 36),
                    {self._TASKS_TARGET_CHECK},
                    {self._TASKS_STAGE_CHECK}
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
            # Spec #49 — queue auto-healing. ``error_class`` refines the binary
            # transient/fatal ``error_type`` into 'infra' | 'item' | 'fatal'
            # (see error_classifier.classify_error_class); the healer loop only
            # ever requeues 'infra' rows. ``heal_attempts`` / ``last_heal_at``
            # bound that second look so a genuine poison message can't loop
            # forever. All additive + defaulted; existing rows backfill to NULL
            # (label-only — they are not retroactively healed).
            self._migrate_add_column(conn, "tasks", "error_class", "TEXT NULL")
            self._migrate_add_column(conn, "tasks", "heal_attempts", "INTEGER DEFAULT 0")
            self._migrate_add_column(conn, "tasks", "last_heal_at", "TIMESTAMP NULL")
            # Spec #48 — podcast-scoped target column. Added without the inline
            # FK / CHECK (SQLite ALTER cannot add either); legacy DBs always
            # trip the rebuild below — which installs ``episode_id`` nullable,
            # the FK, and the exactly-one-target CHECK — because their stored
            # DDL still carries ``episode_id TEXT NOT NULL``.
            self._migrate_add_column(conn, "tasks", "podcast_id", "TEXT NULL")

            # Spec #28 §0.5 — rebuild the tasks table whenever the existing
            # CHECK constraint is missing any current ``TaskStage`` value.
            # SQLite has no ``ALTER TABLE ... DROP CONSTRAINT`` so the only
            # way to widen a CHECK is the standard rebuild dance: create
            # _new with the new constraint, copy rows, drop old, rename.
            # ``CREATE TABLE IF NOT EXISTS`` above is a no-op for legacy
            # databases and would not update the constraint by itself.
            #
            # Guard: read ``sqlite_master.sql`` and look for each enum
            # value as a quoted token (``'extract-entities'``) rather than
            # a bare word — a bare-word match would false-positive on
            # column names and false-negative if a later stage shares a
            # substring with an earlier one. Quoted matching is exact and
            # mirrors the form SQLite stores the CHECK clause in. If the
            # enum gains a new stage between releases (as happened when
            # ``rebuild-cooccurrences`` was added after the initial
            # extract-entities migration), the next startup widens the
            # constraint automatically.
            cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'")
            row = cursor.fetchone()
            existing_ddl = row["sql"] if row else None
            if existing_ddl:
                missing_stages = [s.value for s in TaskStage if f"'{s.value}'" not in existing_ddl]
                # Spec #48 — legacy DBs carry ``episode_id TEXT NOT NULL`` and no
                # exactly-one-target CHECK. Detecting either (the stored DDL
                # normalises whitespace, so match the canonical token) forces the
                # same rebuild dance: only a table rebuild can drop the NOT NULL
                # and install the FK + target CHECK.
                needs_target_migration = (
                    "episode_id TEXT NOT NULL" in existing_ddl or "(episode_id IS NOT NULL)" not in existing_ddl
                )
                if missing_stages or needs_target_migration:
                    logger.info(
                        "Migrating tasks table: rebuild (stage CHECK and/or podcast target)",
                        missing_stages=missing_stages,
                        target_migration=needs_target_migration,
                    )
                    self._rebuild_tasks_with_extended_check(conn)
                    logger.info(
                        "Migration complete: tasks table rebuilt",
                        added_stages=missing_stages,
                        target_migration=needs_target_migration,
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
            # Per-stage polling: get_next_task(stage=X) runs 5×/poll-interval
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_stage_status_priority
                ON tasks(stage, status, priority DESC, created_at ASC)
            """
            )
            # Spec #48 — feed-scoped lookups: the enqueue uniqueness guard
            # (``has_pending_feed_task``) and the per-podcast active filter.
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_podcast_stage
                ON tasks(podcast_id, stage)
                WHERE podcast_id IS NOT NULL
            """
            )

            logger.debug("Tasks table ensured")

    def _rebuild_tasks_with_extended_check(self, conn: sqlite3.Connection) -> None:
        """SQLite table rebuild: widen ``tasks.stage`` CHECK constraint.

        The ``CREATE TABLE IF NOT EXISTS`` that runs on every startup is
        a no-op when the table already exists, so it cannot widen the
        old 5-stage CHECK on legacy databases. SQLite has no
        ``ALTER TABLE ... DROP CONSTRAINT``, so the standard fix is to
        copy into a new table with the desired constraint, drop the old
        one, and rename. We do this inside the connection's existing
        transaction so a partial failure rolls back cleanly.

        After the rename the indexes are gone (they belonged to the old
        table); the caller's index ``CREATE INDEX IF NOT EXISTS`` block
        recreates them on the next pass.
        """
        # If a previous rebuild crashed between ``CREATE TABLE
        # tasks_new_spec28`` and ``ALTER TABLE … RENAME``, the orphan
        # table will still be on disk (DDL persists in WAL even if the
        # whole transaction was meant to roll back). Drop it first so
        # the rebuild is genuinely re-runnable.
        conn.execute("DROP TABLE IF EXISTS tasks_new_spec28")
        conn.execute(
            f"""
            CREATE TABLE tasks_new_spec28 (
                id TEXT PRIMARY KEY NOT NULL,
                episode_id TEXT NULL,
                podcast_id TEXT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER DEFAULT 0,
                error_message TEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP NULL,
                completed_at TIMESTAMP NULL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                next_retry_at TIMESTAMP NULL,
                error_type TEXT NULL,
                last_error TEXT NULL,
                metadata TEXT NULL,
                error_class TEXT NULL,
                heal_attempts INTEGER DEFAULT 0,
                last_heal_at TIMESTAMP NULL,
                FOREIGN KEY (episode_id) REFERENCES episodes(id),
                FOREIGN KEY (podcast_id) REFERENCES podcasts(id),
                CHECK (length(id) = 36),
                {self._TASKS_TARGET_CHECK},
                {self._TASKS_STAGE_CHECK}
            )
            """
        )
        # Copy every column we explicitly enumerated in the new DDL —
        # listing them by name keeps the migration robust against future
        # column additions on either side. ``podcast_id`` is copied too: it
        # was ALTER-added before this rebuild fired, so it exists on the
        # source table (NULL for every legacy/episode-scoped row, which
        # satisfies the exactly-one-target CHECK alongside their episode_id).
        conn.execute(
            """
            INSERT INTO tasks_new_spec28 (
                id, episode_id, podcast_id, stage, status, priority, error_message,
                created_at, updated_at, started_at, completed_at,
                retry_count, max_retries, next_retry_at, error_type,
                last_error, metadata, error_class, heal_attempts, last_heal_at
            )
            SELECT
                id, episode_id, podcast_id, stage, status, priority, error_message,
                created_at, updated_at, started_at, completed_at,
                retry_count, max_retries, next_retry_at, error_type,
                last_error, metadata, error_class, heal_attempts, last_heal_at
            FROM tasks
            """
        )
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new_spec28 RENAME TO tasks")

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
            podcast_id=row["podcast_id"] if "podcast_id" in row.keys() else None,
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
            error_class=(row["error_class"] if "error_class" in row.keys() else None),
            heal_attempts=((row["heal_attempts"] or 0) if "heal_attempts" in row.keys() else 0),
            last_heal_at=(
                datetime.fromisoformat(row["last_heal_at"])
                if "last_heal_at" in row.keys() and row["last_heal_at"]
                else None
            ),
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
        now = now_utc().isoformat()
        metadata = metadata or {}
        max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        metadata_json = json.dumps(metadata) if metadata else None

        def _write() -> None:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO tasks (id, episode_id, stage, status, priority, max_retries, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                    (task_id, episode_id, stage.value, priority, max_retries, metadata_json, now, now),
                )

        self._exec_with_lock_retry("add_task", _write)

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

    def add_feed_task(
        self,
        podcast_id: str,
        stage: TaskStage = TaskStage.REFRESH_FEED,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[Task]:
        """Enqueue a podcast-scoped task (spec #48), e.g. REFRESH_FEED.

        Carries ``podcast_id`` with ``episode_id`` NULL. Applies the per-feed
        coalescing **uniqueness guard**: if a non-terminal task for this
        (podcast, stage) already exists, returns ``None`` instead of enqueuing
        a duplicate — two concurrent refreshes of one feed would double-fetch
        and race on the cache-header write.

        Returns the created ``Task``, or ``None`` if coalesced.
        """
        if not is_feed_scoped_stage(stage):
            raise ValueError(f"add_feed_task is for feed-scoped stages only, got {stage.value}")

        task_id = str(uuid.uuid4())
        now = now_utc().isoformat()
        metadata = metadata or {}
        max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        metadata_json = json.dumps(metadata) if metadata else None
        created: list[bool] = [False]

        def _write() -> None:
            with self._get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    # Uniqueness guard inside the write txn so two schedulers
                    # racing the same feed can't both insert.
                    exists = conn.execute(
                        """
                        SELECT 1 FROM tasks
                        WHERE podcast_id = ? AND stage = ?
                          AND status IN ('pending', 'processing', 'retry_scheduled')
                        LIMIT 1
                        """,
                        (podcast_id, stage.value),
                    ).fetchone()
                    if exists:
                        conn.rollback()
                        return
                    conn.execute(
                        """
                        INSERT INTO tasks (id, episode_id, podcast_id, stage, status, priority, max_retries, metadata, created_at, updated_at)
                        VALUES (?, NULL, ?, ?, 'pending', ?, ?, ?, ?, ?)
                        """,
                        (task_id, podcast_id, stage.value, priority, max_retries, metadata_json, now, now),
                    )
                    created[0] = True
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

        self._exec_with_lock_retry("add_feed_task", _write)

        if not created[0]:
            logger.debug("feed_task_coalesced", podcast_id=podcast_id, stage=stage.value)
            return None

        logger.info(f"Feed task queued: {task_id} - {stage.value} for podcast {podcast_id}")
        return Task(
            id=task_id,
            episode_id=None,
            podcast_id=podcast_id,
            stage=stage,
            status=TaskStatus.PENDING,
            priority=priority,
            max_retries=max_retries,
            metadata=metadata,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

    def has_pending_feed_task(self, podcast_id: str, stage: TaskStage = TaskStage.REFRESH_FEED) -> bool:
        """True if a non-terminal feed task exists for this (podcast, stage)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE podcast_id = ? AND stage = ? AND status IN ('pending', 'processing', 'retry_scheduled')
                LIMIT 1
                """,
                (podcast_id, stage.value),
            )
            return cursor.fetchone() is not None

    def get_next_task(
        self,
        stage: Optional[TaskStage] = None,
        exclude_episode_ids: Optional[set[str]] = None,
        exclude_podcast_ids: Optional[set[str]] = None,
    ) -> Optional[Task]:
        """
        Get the next pending or ready-to-retry task, atomically marking it as 'processing'.

        This considers both:
        - Tasks with status='pending'
        - Tasks with status='retry_scheduled' where next_retry_at <= now

        Args:
            stage: Optionally filter to a specific stage
            exclude_episode_ids: Episode IDs to skip (already being processed)
            exclude_podcast_ids: Podcast IDs to skip — the per-podcast mutex for
                feed-scoped (REFRESH_FEED) tasks (spec #48)

        Returns:
            Task if one is available, None otherwise
        """
        with self._get_connection() as conn:
            now = now_utc().isoformat()

            # SQLite doesn't have UPDATE...RETURNING, so we use a transaction
            # with immediate locking to prevent race conditions
            conn.execute("BEGIN IMMEDIATE")

            try:
                # Build query with optional filters
                conditions = ["(status = 'pending' OR (status = 'retry_scheduled' AND next_retry_at <= ?))"]
                params: list = [now]

                if stage:
                    conditions.append("stage = ?")
                    params.append(stage.value)

                if exclude_episode_ids:
                    placeholders = ",".join("?" for _ in exclude_episode_ids)
                    # Spec #48 — guard the NULL: a feed task has episode_id NULL,
                    # and ``NULL NOT IN (…)`` is NULL (not TRUE) in SQLite, which
                    # would wrongly filter every REFRESH_FEED row out whenever any
                    # episode task is active. The ``IS NULL`` arm keeps feed tasks
                    # claimable through the episode-exclusion filter.
                    conditions.append(f"(episode_id IS NULL OR episode_id NOT IN ({placeholders}))")
                    params.extend(exclude_episode_ids)

                if exclude_podcast_ids:
                    placeholders = ",".join("?" for _ in exclude_podcast_ids)
                    conditions.append(f"(podcast_id IS NULL OR podcast_id NOT IN ({placeholders}))")
                    params.extend(exclude_podcast_ids)

                where = " AND ".join(conditions)
                cursor = conn.execute(
                    f"""
                    SELECT * FROM tasks
                    WHERE {where}
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """,
                    params,
                )

                row = cursor.fetchone()
                if not row:
                    conn.rollback()
                    return None

                task_id = row["id"]

                # Spec #25 item 3.6 — defence-in-depth conditional UPDATE.
                # ``BEGIN IMMEDIATE`` already serialises writers, but the
                # ``status IN (...)`` predicate makes a double-claim
                # structurally impossible: any second writer that somehow
                # got past the SELECT (e.g. WAL read snapshots predating
                # the first writer's commit) sees ``rowcount == 0`` and
                # rolls back instead of stomping on the in-flight task.
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'processing', started_at = ?, updated_at = ?
                    WHERE id = ?
                      AND status IN ('pending', 'retry_scheduled')
                    """,
                    (now, now, task_id),
                )

                if cursor.rowcount == 0:
                    # Another worker raced ahead and claimed the same row
                    # between our SELECT and UPDATE. Drop our intended
                    # claim and let the caller poll again.
                    conn.rollback()
                    logger.debug("task_claim_lost_race", task_id=task_id)
                    return None

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
        now = now_utc().isoformat()

        def _write() -> None:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed', completed_at = ?, updated_at = ?
                    WHERE id = ?
                """,
                    (now, now, task_id),
                )

        self._exec_with_lock_retry("complete_task", _write)

        logger.info(f"Task completed: {task_id}")

    def supersede_stale_tasks(self, episode_id: str, completed_stage: TaskStage) -> int:
        """Auto-resolve dead/failed DLQ rows that ``completed_stage`` makes moot.

        When the same episode reaches a later stage through a fresh run
        (e.g. the user fixed a bad API key, re-triggered transcription,
        and the pipeline made it to ``summarize``), the older
        ``transcribe`` DLQ row is obsolete: a Retry on it would race the
        current state, and stale rows train users to ignore the queue.

        The user chain (``download``..``summarize``) and the entity
        branch (``extract-entities``..``reindex``) are independent failure
        domains, so this function only marks DLQ rows for stages in the
        same branch at or before ``completed_stage``.

        Returns the number of rows marked ``superseded``.
        """
        in_branch = _stages_at_or_before(completed_stage)
        if not in_branch:
            return 0
        now = now_utc().isoformat()
        placeholders = ",".join("?" * len(in_branch))
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = 'superseded', updated_at = ?, completed_at = ?
                WHERE episode_id = ?
                  AND stage IN ({placeholders})
                  AND status IN ('dead', 'failed')
                """,
                [now, now, episode_id, *(s.value for s in in_branch)],
            )
            count = cursor.rowcount or 0
        if count:
            logger.info(
                "dlq_tasks_superseded",
                episode_id=episode_id,
                completed_stage=completed_stage.value,
                count=count,
            )
        return count

    def fail_task(self, task_id: str, error_message: str) -> None:
        """
        Mark a task as failed (explicit/manual terminal fail, e.g. user cancel).

        Spec #49: clears ``error_class`` so the auto-healer never resurrects a
        task that was failed on purpose. The healer only requeues
        ``error_class='infra'`` rows; a retry_scheduled task carrying a prior
        'infra' label that the user cancels must NOT be silently re-queued after
        cooldown. (The retry-exhaustion path in ``schedule_retry`` is what sets
        a healable label — not this explicit fail.)

        Args:
            task_id: ID of the task that failed
            error_message: Error description
        """
        now = now_utc().isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = ?,
                    error_class = NULL,
                    completed_at = ?,
                    updated_at = ?
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

    def claim_pending_for_coalescing(self, stage: TaskStage) -> List[str]:
        """Atomically mark all ``pending`` rows for ``stage`` as completed
        and return their ``episode_id``s.

        Used by handlers whose work can be batched across episodes — the
        cooccurrence rebuild folds many per-episode rows into one
        corpus-scoped run. The handler holds a process lock while
        calling this, so the claim+rebuild pair is effectively atomic
        from the perspective of other workers.

        Only ``pending`` rows are claimed: ``processing`` rows belong to
        peer workers actively running the same handler, and ``retry_scheduled``
        rows are waiting on backoff and may yet succeed on their own.
        """
        now = now_utc().isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                UPDATE tasks
                SET status = ?, completed_at = ?, updated_at = ?
                WHERE stage = ? AND status = ?
                RETURNING episode_id
                """,
                (TaskStatus.COMPLETED.value, now, now, stage.value, TaskStatus.PENDING.value),
            ).fetchall()
        return [r["episode_id"] for r in rows]

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
        Delete completed/failed/dead/superseded tasks older than specified days.

        Args:
            days: Delete tasks older than this many days

        Returns:
            Number of tasks deleted
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'failed', 'dead', 'superseded')
                AND julianday(completed_at) < julianday('now', '-' || ? || ' days')
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
        now = now_utc().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', started_at = NULL, updated_at = ?
                WHERE status = 'processing'
                -- ``started_at`` is stored as ``now_utc().isoformat()`` (ISO-8601
                -- with a 'T' separator + ``+00:00``), but ``datetime('now', …)``
                -- renders a space-separated, tz-naive string. A TEXT ``<``
                -- compares lexicographically: 'T'(84) > ' '(32) at the separator,
                -- so EVERY stored value sorts after the cutoff and the predicate
                -- matched zero rows — the watchdog silently never fired. Compare
                -- as numbers via ``julianday`` so format/offset don't matter.
                AND julianday(started_at) < julianday('now', '-' || ? || ' minutes')
            """,
                (now, timeout_minutes),
            )

            reset = cursor.rowcount
            if reset > 0:
                logger.warning(f"Reset {reset} stale tasks back to pending")

            return reset

    def recover_interrupted_tasks(self, excluded_stages: Optional[List[TaskStage]] = None) -> int:
        """
        Recover tasks left in 'processing' status by a server restart or crash.

        Called on startup. Spec #49 Layer 4 splits recovery by stage idempotency:

        - **Idempotent stages** (``is_idempotent_stage`` — the user chain
          download→summarize plus REFRESH_FEED) are reset to ``pending`` and
          RESUME. They re-run deterministically from durable upstream artifacts,
          so a restart mid-pipeline should pick up where it left off rather than
          stranding re-runnable work in the DLQ for a human to retry.
        - **Non-idempotent stages** (currently the entity branch) are marked
          ``failed`` — the conservative legacy behaviour, preserved where
          auto-resume isn't clearly safe.
        - **Excluded stages** are left untouched in ``processing`` (e.g. a cloud
          transcribe whose remote job may still be running); excluding a stage
          overrides its idempotent auto-resume.

        Args:
            excluded_stages: Stages to NOT recover at all — left in
                ``processing`` (cloud tasks that may still be running remotely).

        Returns:
            Total interrupted tasks recovered (resumed + failed).
        """
        now = now_utc().isoformat()
        excluded = set(excluded_stages or [])

        # Idempotent + not explicitly excluded → resume. Excluding a stage wins
        # over its idempotent auto-resume (the caller knows it's unsafe here).
        resume_values = [s.value for s in _IDEMPOTENT_STAGES if s not in excluded]
        # Stages we must NOT mark failed: the ones we just resumed + the
        # explicitly excluded. Everything else still in 'processing' is failed.
        skip_fail_values = resume_values + [s.value for s in excluded]

        with self._get_connection() as conn:
            resumed = 0
            if resume_values:
                placeholders = ",".join("?" * len(resume_values))
                cursor = conn.execute(
                    f"""
                    UPDATE tasks
                    SET status = 'pending', started_at = NULL, updated_at = ?
                    WHERE status = 'processing' AND stage IN ({placeholders})
                    """,
                    (now, *resume_values),
                )
                resumed = cursor.rowcount

            if skip_fail_values:
                placeholders = ",".join("?" * len(skip_fail_values))
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
                    (now, now, *skip_fail_values),
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
            failed = cursor.rowcount

            if resumed > 0:
                logger.info(f"Recovered {resumed} interrupted task(s) - resumed (idempotent)")
            if failed > 0:
                logger.warning(f"Recovered {failed} interrupted task(s) - marked as failed")

            return resumed + failed

    def schedule_retry(self, task_id: str, error_message: str, error_class: Optional[str] = None) -> Optional[Task]:
        """
        Schedule a task for retry with exponential backoff.

        If the task has exceeded max_retries, it will be marked as FAILED instead.

        Args:
            task_id: ID of the task to retry
            error_message: The error that caused the failure
            error_class: Spec #49 attribution ('infra' | 'item'); persisted on
                the row so the healer loop can later find infra-class ``failed``
                tasks. ``None`` leaves the column unchanged (legacy callers).

        Returns:
            Updated Task object, or None if task not found
        """
        now = now_utc()
        now_iso = now.isoformat()

        def _write() -> None:
            with self._get_connection() as conn:
                # Get current task state
                cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                row = cursor.fetchone()
                if not row:
                    logger.warning(f"Cannot schedule retry for unknown task: {task_id}")
                    return

                current_retry = row["retry_count"] or 0
                max_retries = row["max_retries"] or self.DEFAULT_MAX_RETRIES
                new_retry_count = current_retry + 1

                # Spec #49 — persist the infra/item attribution alongside the
                # binary error_type. COALESCE keeps any existing label when the
                # caller passes None, so we never clobber a prior classification.
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
                            error_class = COALESCE(?, error_class),
                            completed_at = ?,
                            updated_at = ?
                        WHERE id = ?
                    """,
                        (new_retry_count, error_message, error_message, error_class, now_iso, now_iso, task_id),
                    )
                    logger.warning(
                        f"Task {task_id} exhausted retries ({new_retry_count}/{max_retries}), marked as failed"
                    )
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
                            error_class = COALESCE(?, error_class),
                            started_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                    """,
                        (new_retry_count, next_retry_iso, error_message, error_class, now_iso, task_id),
                    )
                    logger.info(
                        f"Task {task_id} scheduled for retry {new_retry_count}/{max_retries} "
                        f"at {next_retry_iso} (in {backoff.total_seconds():.0f}s)"
                    )

        self._exec_with_lock_retry("schedule_retry", _write)
        # Return updated task (read can hit the same lock; retry through the helper too).
        return self._exec_with_lock_retry("schedule_retry_readback", lambda: self.get_task(task_id))

    def reschedule_without_budget(
        self, task_id: str, error_message: str, error_class: Optional[str] = None
    ) -> Optional[Task]:
        """Re-queue a task WITHOUT charging its retry budget (spec #49 L1).

        Used when an infra-class failure occurs while the stage's circuit
        breaker is open/half-open: the failure is the dependency's fault, not
        the item's, so ``retry_count`` is left untouched. The row goes back to
        ``retry_scheduled`` eligible immediately (``next_retry_at = now``); the
        breaker — not a backoff timer — is what keeps the poller from
        re-dispatching it until the dependency recovers.

        Args:
            task_id: ID of the task to park.
            error_message: The infra error that tripped/held the breaker.
            error_class: Attribution to persist (typically 'infra').

        Returns:
            Updated Task, or None if not found.
        """
        now = now_utc().isoformat()

        def _write() -> None:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'retry_scheduled',
                        next_retry_at = ?,
                        last_error = ?,
                        error_type = 'transient',
                        error_class = COALESCE(?, error_class),
                        started_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                """,
                    (now, error_message, error_class, now, task_id),
                )

        self._exec_with_lock_retry("reschedule_without_budget", _write)
        return self._exec_with_lock_retry("reschedule_without_budget_readback", lambda: self.get_task(task_id))

    def mark_dead(self, task_id: str, error_message: str, error_class: Optional[str] = None) -> Optional[Task]:
        """
        Move a task to the Dead Letter Queue (status='dead').

        Use this for fatal errors that will never succeed on retry.

        Args:
            task_id: ID of the task to mark as dead
            error_message: The fatal error description
            error_class: Spec #49 attribution; defaults to 'fatal' since a dead
                task is by definition fatal. Recorded for queue-viewer parity
                with retry/failed rows. Never healed (the healer skips 'dead').

        Returns:
            Updated Task object, or None if task not found
        """
        now = now_utc().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'dead',
                    error_message = ?,
                    error_type = 'fatal',
                    error_class = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
            """,
                (error_message, error_class or "fatal", now, now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot mark unknown task as dead: {task_id}")
                return None

        logger.error(f"Task {task_id} moved to DLQ (dead): {error_message}")
        return self.get_task(task_id)

    def get_dead_tasks(
        self,
        limit: int = 100,
        stage_filter: Optional[List[TaskStage]] = None,
    ) -> List[Task]:
        """
        Get tasks in the Dead Letter Queue (terminal failure states).

        Includes both 'dead' (fatal errors) and 'failed' (transient errors
        that exhausted retries) tasks.

        Args:
            limit: Maximum number of tasks to return
            stage_filter: If provided, restrict results to these stages.
                Spec #28 Phase 3.2 uses this to separate the entity branch
                from the user-facing critical path in the queue viewer.

        Returns:
            List of dead/failed tasks, ordered by most recent first
        """
        sql = "SELECT * FROM tasks WHERE status IN ('dead', 'failed')"
        params: List[Any] = []
        if stage_filter:
            placeholders = ",".join("?" * len(stage_filter))
            sql += f" AND stage IN ({placeholders})"
            params.extend(s.value for s in stage_filter)
        sql += " ORDER BY completed_at DESC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def retry_dead_task(self, task_id: str) -> Optional[Task]:
        """
        Move a dead or failed task back to pending for manual retry.

        Args:
            task_id: ID of the dead/failed task to retry

        Returns:
            Updated Task object, or None if task not found or not in terminal state
        """
        now = now_utc().isoformat()

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
                WHERE id = ? AND status IN ('dead', 'failed')
            """,
                (now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(
                    "Cannot retry task: not found or not in terminal state",
                    task_id=task_id,
                )
                return None

        logger.info("Task moved from DLQ back to pending", task_id=task_id)
        return self.get_task(task_id)

    def find_healable_tasks(
        self,
        *,
        cooldown: timedelta,
        max_heal_attempts: int,
        limit: int = 100,
    ) -> List[Task]:
        """
        Find ``failed`` tasks eligible for an auto-heal requeue (spec #49 L3).

        A task is healable when all hold:

        - ``status='failed'`` — the healer NEVER touches ``dead`` (fatal means
          fatal) or any live/pending state.
        - ``error_class='infra'`` — only shared-dependency outages get a second
          look; per-item ('item') failures stay terminal until a human acts.
        - ``heal_attempts < max_heal_attempts`` — bounds the loop so a
          permanently-broken dependency can't requeue forever.
        - a ``cooldown`` has elapsed since both the last failure
          (``completed_at``) and the last heal (``last_heal_at``) — avoids
          re-requeuing into a dependency that is still down.

        Args:
            cooldown: Minimum age since last failure/heal before re-requeuing.
            max_heal_attempts: Per-task cap on auto-heal rounds.
            limit: Max rows to return per sweep (staged re-admission).

        Returns:
            Healable tasks, oldest failure first (drain the longest-stuck first).
        """
        cutoff = (now_utc() - cooldown).isoformat()
        sql = """
            SELECT * FROM tasks
            WHERE status = 'failed'
              AND error_class = 'infra'
              AND COALESCE(heal_attempts, 0) < ?
              AND (completed_at IS NULL OR completed_at <= ?)
              AND (last_heal_at IS NULL OR last_heal_at <= ?)
            ORDER BY completed_at ASC
            LIMIT ?
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (max_heal_attempts, cutoff, cutoff, limit))
            return [self._row_to_task(row) for row in cursor.fetchall()]

    def heal_task(self, task_id: str, max_heal_attempts: int) -> Optional[Task]:
        """
        Auto-heal a single ``failed`` infra-class task back to ``pending``.

        The healer-loop analogue of the manual ``retry_dead_task``, but: it
        only ever transitions ``failed`` + ``error_class='infra'`` rows (never
        ``dead``), it increments ``heal_attempts`` and stamps ``last_heal_at``,
        and it re-checks the cap inside the UPDATE's WHERE clause so a
        concurrent sweep cannot push a row past ``max_heal_attempts``.

        ``retry_count`` is reset to 0 so the requeued task gets a fresh retry
        budget against the (hopefully recovered) dependency. ``error_class`` is
        intentionally left intact: if the dependency is still down the task
        re-fails as infra and remains healable until the cap is hit.

        Args:
            task_id: ID of the failed infra-class task to requeue.
            max_heal_attempts: Per-task cap; the transition no-ops at the cap.

        Returns:
            Updated Task (now ``pending``), or None if not eligible.
        """
        now = now_utc().isoformat()

        def _write() -> int:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending',
                        retry_count = 0,
                        error_message = NULL,
                        last_error = NULL,
                        next_retry_at = NULL,
                        started_at = NULL,
                        completed_at = NULL,
                        heal_attempts = COALESCE(heal_attempts, 0) + 1,
                        last_heal_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status = 'failed'
                      AND error_class = 'infra'
                      AND COALESCE(heal_attempts, 0) < ?
                """,
                    (now, now, task_id, max_heal_attempts),
                )
                return cursor.rowcount

        rowcount = self._exec_with_lock_retry("heal_task", _write)
        if rowcount == 0:
            logger.warning(
                "queue_heal_skip",
                task_id=task_id,
                note="not failed/infra or heal cap reached",
            )
            return None

        healed = self.get_task(task_id)
        logger.info(
            "queue_heal_requeued",
            task_id=task_id,
            stage=healed.stage.value if healed else None,
            heal_attempts=healed.heal_attempts if healed else None,
        )
        return healed

    def cancel_retry(self, task_id: str) -> Optional[Task]:
        """
        Cancel a scheduled retry, marking the task as failed.

        Spec #49: clears ``error_class`` (and the now-moot ``next_retry_at``) so
        the auto-healer never resurrects a retry the user explicitly cancelled.
        A ``retry_scheduled`` task can carry an 'infra' label from
        ``schedule_retry``; without this, the healer — which requeues
        ``status='failed'`` + ``error_class='infra'`` rows after a cooldown —
        would silently re-queue it. Mirrors ``fail_task``.

        Args:
            task_id: ID of the task to cancel

        Returns:
            Updated Task object, or None if task not found or not in retry_scheduled status
        """
        now = now_utc().isoformat()

        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = COALESCE(last_error, 'Retry cancelled by user'),
                    error_class = NULL,
                    next_retry_at = NULL,
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

    def bump_task(self, task_id: str) -> bool:
        """
        Move a pending task to the front of the queue by setting highest priority.

        Args:
            task_id: ID of the task to bump

        Returns:
            True if the task was bumped, False if not found or not pending
        """
        now = now_utc().isoformat()

        with self._get_connection() as conn:
            # Get max current priority among pending tasks
            cursor = conn.execute("SELECT MAX(priority) FROM tasks WHERE status = 'pending'")
            row = cursor.fetchone()
            max_priority = row[0] if row and row[0] is not None else 0

            # Set this task's priority higher
            cursor = conn.execute(
                """
                UPDATE tasks
                SET priority = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
            """,
                (max_priority + 1, now, task_id),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot bump task {task_id}: not found or not pending")
                return False

        logger.info(f"Task {task_id} bumped to priority {max_priority + 1}")
        return True

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a pending task by deleting it from the queue.

        This only works for tasks in 'pending' status. Tasks that are
        already processing cannot be cancelled.

        Args:
            task_id: ID of the task to cancel

        Returns:
            True if the task was cancelled, False if not found or not pending
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE id = ? AND status = 'pending'
            """,
                (task_id,),
            )

            if cursor.rowcount == 0:
                logger.warning(f"Cannot cancel task {task_id}: not found or not pending")
                return False

        logger.info(f"Task {task_id} cancelled and removed from queue")
        return True

    def get_active_tasks(self, include_completed: int = 10) -> Dict[str, List[Task]]:
        """
        Get all active and recently completed tasks for the queue viewer.

        Args:
            include_completed: Number of recently completed tasks to include

        Returns:
            Dictionary with:
            - pending: List of pending tasks (ordered by priority DESC, created_at ASC)
            - processing: List of processing tasks (should be 0 or 1)
            - retry_scheduled: List of tasks waiting for retry
            - completed: List of recently completed tasks (newest first)
        """
        with self._get_connection() as conn:
            result: Dict[str, List[Task]] = {
                "pending": [],
                "processing": [],
                "retry_scheduled": [],
                "completed": [],
            }

            # Pending tasks
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 100
            """
            )
            result["pending"] = [self._row_to_task(row) for row in cursor.fetchall()]

            # Processing tasks
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'processing'
                ORDER BY started_at ASC
            """
            )
            result["processing"] = [self._row_to_task(row) for row in cursor.fetchall()]

            # Retry scheduled tasks
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'retry_scheduled'
                ORDER BY next_retry_at ASC
                LIMIT 50
            """
            )
            result["retry_scheduled"] = [self._row_to_task(row) for row in cursor.fetchall()]

            # Recently completed tasks
            cursor = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'completed'
                ORDER BY completed_at DESC
                LIMIT ?
            """,
                (include_completed,),
            )
            result["completed"] = [self._row_to_task(row) for row in cursor.fetchall()]

            return result
