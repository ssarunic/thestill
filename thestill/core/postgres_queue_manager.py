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
PostgreSQL-based task queue (spec #44 Phase 3).

Port of :class:`thestill.core.queue_manager.QueueManager` to Postgres with the
same public surface and semantics. The domain vocabulary (``TaskStage``,
``TaskStatus``, ``ErrorType``, ``Task``, ``calculate_backoff``, the stage
graph helpers) is imported from ``queue_manager`` — it is engine-agnostic and
deliberately NOT duplicated here.

Dialect/concurrency differences from the SQLite version:

- **The claim** (:meth:`get_next_task`) is the canonical single-statement
  Postgres job-queue pattern: a CTE ``SELECT ... FOR UPDATE SKIP LOCKED``
  feeding an ``UPDATE ... RETURNING``. MVCC + row locks replace SQLite's
  ``BEGIN IMMEDIATE`` select-then-conditional-update dance, and concurrent
  workers skip each other's in-flight claims instead of serialising.
- **No lock-retry machinery**: there is no ``database is locked`` in Postgres;
  ``_exec_with_lock_retry`` / ``busy_timeout`` have no analogue and statements
  are executed directly. Each method keeps the same transaction boundary as
  the SQLite version (the psycopg connection context commits on clean exit).
- **No DDL**: the ``tasks`` table (uuid ids, timestamptz stamps, jsonb
  metadata) is owned by ``repositories/postgres_schema.py``.
- Port conventions per ``utils/postgres_ext.py``: ``%s`` placeholders, uuid
  str params + ``as_str()`` on reads, tz-aware datetimes for timestamptz
  (never isoformat strings — EXCEPT the claim-lease token, which arrives from
  the worker as the ISO string of the claim's ``started_at`` and is passed as
  text for Postgres to cast), ``Jsonb`` for metadata writes / direct dict
  reads.
- ``add_feed_task``'s uniqueness guard uses a transaction-scoped advisory
  lock keyed on (podcast, stage) — the per-key equivalent of SQLite's
  ``BEGIN IMMEDIATE`` writer serialisation.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb
from structlog import get_logger

from ..models.podcast import EpisodeState
from ..utils.datetime_utils import now_utc
from ..utils.postgres_ext import as_str, connect
from .queue_manager import (
    _IDEMPOTENT_STAGES,
    ErrorType,
    Task,
    TaskStage,
    TaskStatus,
    calculate_backoff,
    is_feed_scoped_stage,
    stages_at_or_before,
    starting_stage_for,
)

logger = get_logger(__name__)


class PostgresQueueManager:
    """
    PostgreSQL-based task queue with the same interface as ``QueueManager``.

    Thread-safety: connection-per-operation; atomicity comes from single
    statements (the claim) and per-method transactions, not client-side locks.
    """

    # Default retry configuration
    DEFAULT_MAX_RETRIES = 3

    def __init__(self, dsn: str):
        """
        Initialize queue manager.

        Args:
            dsn: psycopg connection string, e.g.
                ``postgresql://user:pass@host:5432/thestill``. The ``tasks``
                table must already exist (``postgres_schema.ensure_schema``).
        """
        self.dsn = dsn
        logger.info("PostgresQueueManager initialized")

    def _row_to_task(self, row: dict) -> Task:
        """Convert a dict row to a Task object.

        Postgres-native types make this simpler than the SQLite version:
        uuid columns come back as ``uuid.UUID`` (→ str via ``as_str``),
        timestamptz columns as tz-aware datetimes (no parsing), and jsonb
        metadata as a dict already (default ``{}`` when NULL).
        """
        # Parse error_type enum if present
        error_type = None
        if row["error_type"]:
            try:
                error_type = ErrorType(row["error_type"])
            except ValueError:
                logger.warning(f"Unknown error_type '{row['error_type']}' for task {row['id']}")

        return Task(
            id=as_str(row["id"]),
            episode_id=as_str(row["episode_id"]),
            podcast_id=as_str(row["podcast_id"]),
            stage=TaskStage(row["stage"]),
            status=TaskStatus(row["status"]),
            priority=row["priority"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            retry_count=row["retry_count"] or 0,
            max_retries=row["max_retries"] or self.DEFAULT_MAX_RETRIES,
            next_retry_at=row["next_retry_at"],
            error_type=error_type,
            last_error=row["last_error"],
            error_class=row["error_class"],
            heal_attempts=row["heal_attempts"] or 0,
            last_heal_at=row["last_heal_at"],
            metadata=row["metadata"] if row["metadata"] is not None else {},
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
        now = now_utc()
        metadata = metadata or {}
        max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES

        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, episode_id, stage, status, priority, max_retries, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s, %s)
                """,
                (
                    task_id,
                    episode_id,
                    stage.value,
                    priority,
                    max_retries,
                    Jsonb(metadata) if metadata else None,
                    now,
                    now,
                ),
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
            created_at=now,
            updated_at=now,
        )

    def enqueue_full_pipeline(
        self,
        *,
        episode_id: str,
        audio_url: Optional[str],
        transcription_provider: str,
        initiated_by: str,
        priority: int = 10,
    ) -> bool:
        """Enqueue the first stage of an episode's full pipeline, URL-optimized.

        When the transcription provider is Dalston it fetches the audio directly
        from the URL, so an episode that still has its ``audio_url`` SKIPS local
        download/downsample and starts at TRANSCRIBE; otherwise it starts at
        DOWNLOAD. ``run_full_pipeline=True`` keeps clean → summarize → entities
        chaining after the first stage (see ``_maybe_enqueue_next_stage``).

        Idempotent: returns ``False`` without enqueuing when the episode already
        has an active (pending/processing/retry) task for the chosen stage, so it
        is safe to call from multiple delivery paths (refresh, follow-seed,
        publish fan-out) for the same episode.

        Args:
            episode_id: Episode to process.
            audio_url: The episode's source audio URL (may be ``None``).
            transcription_provider: ``config.transcription_provider`` — only
                ``"dalston"`` enables the download-skipping URL path.
            initiated_by: Provenance tag stored in task metadata for tracing.
            priority: Queue priority (default 10 — spec #48 freshness priority).

        Returns:
            ``True`` if a task was enqueued, ``False`` if it was coalesced.
        """
        # DISCOVERED orphan: starting_stage_for applies the Dalston URL shortcut
        # (TRANSCRIBE) or the default DISCOVERED → DOWNLOAD. Never None here.
        initial_stage = (
            starting_stage_for(
                EpisodeState.DISCOVERED,
                transcription_provider=transcription_provider,
                has_audio_url=bool(audio_url),
            )
            or TaskStage.DOWNLOAD
        )
        if self.has_pending_task(episode_id, initial_stage):
            return False
        self.add_task(
            episode_id=episode_id,
            stage=initial_stage,
            priority=priority,
            metadata={"run_full_pipeline": True, "initiated_by": initiated_by},
        )
        return True

    def enqueue_discovered_episodes(
        self,
        *,
        podcast_id: str,
        repository: Any,
        config: Any,
        initiated_by: str,
    ) -> int:
        """Auto-enqueue the full pipeline for a feed's discovered-but-unqueued episodes.

        Shared by both refresh paths — the queued ``handle_refresh_feed`` task
        and the inline ``RefreshService.refresh`` (CLI / web / MCP) — so a
        freshly-refreshed feed begins processing its new episodes the same way
        no matter how the refresh was triggered.

        Drives off DB state (``get_discovered_unqueued_episodes``) rather than an
        in-memory new-episode list, which buys idempotency (already-queued
        episodes are filtered out) and crash-recovery (episodes persisted by a
        prior run that died before enqueuing are repaired on the next refresh)
        for free.

        Initial-backfill cap: a brand-new podcast discovers its ENTIRE back
        catalog in one refresh, but only the most-recent few should auto-process
        on subscribe; the remainder are marked ``auto_process_excluded`` so this
        sweep never enqueues them. An established podcast skips the cap, so every
        genuinely-new episode (and any real crash-orphan) flows through.

        Args:
            podcast_id: Feed whose discovered episodes to enqueue.
            repository: Podcast repository (``get_discovered_unqueued_episodes``,
                ``has_processed_episodes``, ``count_episodes_with_tasks``,
                ``mark_episodes_auto_process_excluded``).
            config: App config (``transcription_provider``, ``inbox_seed_on_follow``).
            initiated_by: Provenance tag stored in task metadata for tracing.

        Returns:
            Number of episodes for which a first-stage task was enqueued.
        """
        provider = getattr(config, "transcription_provider", "")
        discovered = repository.get_discovered_unqueued_episodes(podcast_id)

        if not repository.has_processed_episodes(podcast_id):
            cap = max(
                0,
                getattr(config, "inbox_seed_on_follow", 2) - repository.count_episodes_with_tasks(podcast_id),
            )
            to_enqueue = discovered[:cap]  # discovered is ordered pub_date DESC → most-recent
            skipped = [eid for eid, _ in discovered[cap:]]
            if skipped:
                repository.mark_episodes_auto_process_excluded(skipped)
                logger.info(
                    "refresh_feed_initial_backfill_capped",
                    podcast_id=podcast_id,
                    cap=cap,
                    excluded=len(skipped),
                )
        else:
            to_enqueue = discovered

        enqueued = 0
        for episode_id, audio_url in to_enqueue:
            # spec #48 freshness priority (10) — newly published jumps backfill.
            if self.enqueue_full_pipeline(
                episode_id=episode_id,
                audio_url=audio_url,
                transcription_provider=provider,
                initiated_by=initiated_by,
            ):
                enqueued += 1
        return enqueued

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

        Postgres note: the guard's check-then-insert runs under a
        transaction-scoped advisory lock keyed on (podcast, stage). This is the
        per-key equivalent of the SQLite version's ``BEGIN IMMEDIATE`` — two
        schedulers racing the same feed serialise on the lock, so both cannot
        pass the SELECT and double-insert; unrelated feeds are not blocked.

        Returns the created ``Task``, or ``None`` if coalesced.
        """
        if not is_feed_scoped_stage(stage):
            raise ValueError(f"add_feed_task is for feed-scoped stages only, got {stage.value}")

        task_id = str(uuid.uuid4())
        now = now_utc()
        metadata = metadata or {}
        max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        created = False

        with connect(self.dsn) as conn:
            # Serialise racing schedulers for this (podcast, stage); released
            # automatically at commit/rollback of this connection context.
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"add_feed_task:{podcast_id}:{stage.value}",),
            )
            # Uniqueness guard inside the locked txn so two schedulers
            # racing the same feed can't both insert.
            exists = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE podcast_id = %s AND stage = %s
                  AND status IN ('pending', 'processing', 'retry_scheduled')
                LIMIT 1
                """,
                (podcast_id, stage.value),
            ).fetchone()
            if not exists:
                conn.execute(
                    """
                    INSERT INTO tasks (id, episode_id, podcast_id, stage, status, priority, max_retries, metadata, created_at, updated_at)
                    VALUES (%s, NULL, %s, %s, 'pending', %s, %s, %s, %s, %s)
                    """,
                    (
                        task_id,
                        podcast_id,
                        stage.value,
                        priority,
                        max_retries,
                        Jsonb(metadata) if metadata else None,
                        now,
                        now,
                    ),
                )
                created = True

        if not created:
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
            created_at=now,
            updated_at=now,
        )

    def has_pending_feed_task(self, podcast_id: str, stage: TaskStage = TaskStage.REFRESH_FEED) -> bool:
        """True if a non-terminal feed task exists for this (podcast, stage)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE podcast_id = %s AND stage = %s AND status IN ('pending', 'processing', 'retry_scheduled')
                LIMIT 1
                """,
                (podcast_id, stage.value),
            ).fetchone()
            return row is not None

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

        The claim is a single statement — the canonical Postgres job-queue
        pattern: a CTE selects the winning row ``FOR UPDATE SKIP LOCKED`` (so
        concurrent workers skip each other's in-flight claims instead of
        blocking or double-claiming), and the outer UPDATE transitions it to
        ``processing`` and returns it. MVCC makes SQLite's ``BEGIN IMMEDIATE``
        + defensive conditional-UPDATE dance unnecessary.

        Args:
            stage: Optionally filter to a specific stage
            exclude_episode_ids: Episode IDs to skip (already being processed)
            exclude_podcast_ids: Podcast IDs to skip — the per-podcast mutex for
                feed-scoped (REFRESH_FEED) tasks (spec #48)

        Returns:
            Task if one is available, None otherwise
        """
        conditions = ["(status = 'pending' OR (status = 'retry_scheduled' AND next_retry_at <= now()))"]
        params: list = []

        if stage:
            conditions.append("stage = %s")
            params.append(stage.value)

        if exclude_episode_ids:
            # Spec #48 — guard the NULL: a feed task has episode_id NULL, and
            # ``NULL != ALL(…)`` is NULL (not TRUE), which would wrongly filter
            # every REFRESH_FEED row out whenever any episode task is active.
            # The ``IS NULL`` arm keeps feed tasks claimable through the
            # episode-exclusion filter.
            conditions.append("(episode_id IS NULL OR episode_id != ALL(%s::uuid[]))")
            params.append(sorted(exclude_episode_ids))

        if exclude_podcast_ids:
            conditions.append("(podcast_id IS NULL OR podcast_id != ALL(%s::uuid[]))")
            params.append(sorted(exclude_podcast_ids))

        where = " AND ".join(conditions)

        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                WITH cte AS (
                    SELECT id FROM tasks
                     WHERE {where}
                     ORDER BY priority DESC, created_at ASC
                     LIMIT 1
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE tasks t
                   SET status = 'processing', started_at = now(), updated_at = now()
                  FROM cte WHERE t.id = cte.id
                RETURNING t.*
                """,
                params,
            ).fetchone()

        if not row:
            return None

        task = self._row_to_task(row)
        logger.info(f"Task claimed: {task.id} - {task.stage.value} (retry #{task.retry_count})")
        return task

    def complete_task(self, task_id: str, claim_started_at: Optional[str] = None) -> bool:
        """
        Mark a task as completed.

        Args:
            task_id: ID of the task to complete.
            claim_started_at: Optional lease guard — the ISO ``started_at`` the
                worker was handed when it claimed this task. When provided, the
                row is completed only if it is still ``processing`` under that
                exact claim timestamp. This defeats the watchdog-abandonment
                race: a handler that the per-stage watchdog gave up on keeps
                running in its thread, but its row may since have been requeued
                (stale-task reset) and reclaimed by another worker under a new
                ``started_at``. Without the guard, the revived zombie would
                complete a claim it no longer owns and double-fire the successor
                fan-out. ``None`` = legacy unconditional complete.

        Returns:
            True iff this call actually transitioned the row to ``completed``.
        """
        now = now_utc()

        with connect(self.dsn) as conn:
            if claim_started_at is not None:
                # The lease token is the worker-side ISO string of the claim's
                # started_at; Postgres casts text → timestamptz for the compare.
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed', completed_at = %s, updated_at = %s
                    WHERE id = %s AND status = 'processing' AND started_at = %s
                    """,
                    (now, now, task_id, claim_started_at),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed', completed_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (now, now, task_id),
                )
            applied = cursor.rowcount or 0

        won = applied > 0

        if won:
            logger.info(f"Task completed: {task_id}")
        elif claim_started_at is not None:
            logger.warning(
                "complete_task_claim_lost",
                task_id=task_id,
                note="row no longer owned by this claim (watchdog-abandoned handler); not completing",
            )
        return won

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
        in_branch = stages_at_or_before(completed_stage)
        if not in_branch:
            return 0
        now = now_utc()
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'superseded', updated_at = %s, completed_at = %s
                WHERE episode_id = %s
                  AND stage = ANY(%s)
                  AND status IN ('dead', 'failed')
                """,
                (now, now, episode_id, [s.value for s in in_branch]),
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
        now = now_utc()

        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = %s,
                    error_class = NULL,
                    completed_at = %s,
                    updated_at = %s
                WHERE id = %s
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
        with connect(self.dsn) as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()

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
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE episode_id = %s
                ORDER BY created_at DESC
                """,
                (episode_id,),
            ).fetchall()

            return [self._row_to_task(row) for row in rows]

    def has_pending_task(self, episode_id: str, stage: TaskStage) -> bool:
        """
        Check if an episode already has a pending, processing, or retry_scheduled task for a stage.

        Args:
            episode_id: ID of the episode
            stage: Pipeline stage to check

        Returns:
            True if there's an active task, False otherwise
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE episode_id = %s AND stage = %s AND status IN ('pending', 'processing', 'retry_scheduled')
                LIMIT 1
                """,
                (episode_id, stage.value),
            ).fetchone()

            return row is not None

    def get_pending_count(self) -> int:
        """
        Get the count of pending tasks.

        Returns:
            Number of pending tasks
        """
        with connect(self.dsn) as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM tasks WHERE status = 'pending'").fetchone()
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
        now = now_utc()
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                UPDATE tasks
                SET status = %s, completed_at = %s, updated_at = %s
                WHERE stage = %s AND status = %s
                RETURNING episode_id
                """,
                (
                    TaskStatus.COMPLETED.value,
                    now,
                    now,
                    stage.value,
                    TaskStatus.PENDING.value,
                ),
            ).fetchall()
        return [as_str(r["episode_id"]) for r in rows]

    def get_queue_stats(self) -> dict:
        """
        Get queue statistics.

        Returns:
            Dictionary with queue stats
        """
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS count
                FROM tasks
                GROUP BY status
                """
            ).fetchall()

            stats = {status.value: 0 for status in TaskStatus}
            for row in rows:
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
        cutoff = now_utc() - timedelta(days=days)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE status IN ('completed', 'failed', 'dead', 'superseded')
                AND completed_at < %s
                """,
                (cutoff,),
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
        now = now_utc()
        cutoff = now - timedelta(minutes=timeout_minutes)

        with connect(self.dsn) as conn:
            # timestamptz compares as an instant — the SQLite julianday
            # workaround (text-format comparison foot-gun) has no PG analogue.
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', started_at = NULL, updated_at = %s
                WHERE status = 'processing'
                AND started_at < %s
                """,
                (now, cutoff),
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
        now = now_utc()
        excluded = set(excluded_stages or [])

        # Idempotent + not explicitly excluded → resume. Excluding a stage wins
        # over its idempotent auto-resume (the caller knows it's unsafe here).
        resume_values = [s.value for s in _IDEMPOTENT_STAGES if s not in excluded]
        # Stages we must NOT mark failed: the ones we just resumed + the
        # explicitly excluded. Everything else still in 'processing' is failed.
        skip_fail_values = resume_values + [s.value for s in excluded]

        with connect(self.dsn) as conn:
            resumed = 0
            if resume_values:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'pending', started_at = NULL, updated_at = %s
                    WHERE status = 'processing' AND stage = ANY(%s)
                    """,
                    (now, resume_values),
                )
                resumed = cursor.rowcount

            if skip_fail_values:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        error_message = 'Task interrupted by server restart',
                        completed_at = %s,
                        updated_at = %s
                    WHERE status = 'processing'
                    AND stage <> ALL(%s)
                    """,
                    (now, now, skip_fail_values),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        error_message = 'Task interrupted by server restart',
                        completed_at = %s,
                        updated_at = %s
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

    def schedule_retry(
        self,
        task_id: str,
        error_message: str,
        error_class: Optional[str] = None,
        claim_started_at: Optional[str] = None,
    ) -> Optional[Task]:
        """
        Schedule a task for retry with exponential backoff.

        If the task has exceeded max_retries, it will be marked as FAILED instead.

        Args:
            task_id: ID of the task to retry
            error_message: The error that caused the failure
            error_class: Spec #49 attribution ('infra' | 'item'); persisted on
                the row so the healer loop can later find infra-class ``failed``
                tasks. ``None`` leaves the column unchanged (legacy callers).
            claim_started_at: Optional lease guard (see ``complete_task``). When
                provided, only reschedules while the row is still ``processing``
                under this exact claim timestamp, so a watchdog-abandoned zombie
                can't clobber a row another worker has since reclaimed.

        Returns:
            Updated Task object, or None if task not found / claim lost
        """
        now = now_utc()
        guard = " AND status = 'processing' AND started_at = %s" if claim_started_at is not None else ""

        with connect(self.dsn) as conn:
            # Get current task state
            row = conn.execute("SELECT * FROM tasks WHERE id = %s", (task_id,)).fetchone()
            if not row:
                logger.warning(f"Cannot schedule retry for unknown task: {task_id}")
                applied = 0
            else:
                current_retry = row["retry_count"] or 0
                max_retries = row["max_retries"] or self.DEFAULT_MAX_RETRIES
                new_retry_count = current_retry + 1

                # Spec #49 — persist the infra/item attribution alongside the
                # binary error_type. COALESCE keeps any existing label when the
                # caller passes None, so we never clobber a prior classification.
                if new_retry_count >= max_retries:
                    # Exhausted retries - mark as failed
                    params: list = [
                        new_retry_count,
                        error_message,
                        error_message,
                        error_class,
                        now,
                        now,
                        task_id,
                    ]
                    if claim_started_at is not None:
                        params.append(claim_started_at)
                    updated = conn.execute(
                        f"""
                        UPDATE tasks
                        SET status = 'failed',
                            retry_count = %s,
                            error_message = %s,
                            last_error = %s,
                            error_type = 'transient',
                            error_class = COALESCE(%s, error_class),
                            completed_at = %s,
                            updated_at = %s
                        WHERE id = %s{guard}
                        """,
                        params,
                    )
                    if updated.rowcount:
                        logger.warning(
                            f"Task {task_id} exhausted retries ({new_retry_count}/{max_retries}), marked as failed"
                        )
                    applied = updated.rowcount or 0
                else:
                    # Schedule retry with exponential backoff
                    backoff = calculate_backoff(new_retry_count)
                    next_retry = now + backoff

                    params = [
                        new_retry_count,
                        next_retry,
                        error_message,
                        error_class,
                        now,
                        task_id,
                    ]
                    if claim_started_at is not None:
                        params.append(claim_started_at)
                    updated = conn.execute(
                        f"""
                        UPDATE tasks
                        SET status = 'retry_scheduled',
                            retry_count = %s,
                            next_retry_at = %s,
                            last_error = %s,
                            error_type = 'transient',
                            error_class = COALESCE(%s, error_class),
                            started_at = NULL,
                            updated_at = %s
                        WHERE id = %s{guard}
                        """,
                        params,
                    )
                    if updated.rowcount:
                        logger.info(
                            f"Task {task_id} scheduled for retry {new_retry_count}/{max_retries} "
                            f"at {next_retry.isoformat()} (in {backoff.total_seconds():.0f}s)"
                        )
                    applied = updated.rowcount or 0

        if claim_started_at is not None and not applied:
            logger.warning(
                "schedule_retry_claim_lost",
                task_id=task_id,
                note="row no longer owned by this claim (watchdog-abandoned handler); not rescheduling",
            )
            return None
        # Return updated task.
        return self.get_task(task_id)

    def reschedule_without_budget(
        self,
        task_id: str,
        error_message: str,
        error_class: Optional[str] = None,
        claim_started_at: Optional[str] = None,
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
        now = now_utc()
        guard = " AND status = 'processing' AND started_at = %s" if claim_started_at is not None else ""

        with connect(self.dsn) as conn:
            params: list = [now, error_message, error_class, now, task_id]
            if claim_started_at is not None:
                params.append(claim_started_at)
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = 'retry_scheduled',
                    next_retry_at = %s,
                    last_error = %s,
                    error_type = 'transient',
                    error_class = COALESCE(%s, error_class),
                    started_at = NULL,
                    updated_at = %s
                WHERE id = %s{guard}
                """,
                params,
            )
            applied = cursor.rowcount or 0

        if claim_started_at is not None and not applied:
            logger.warning(
                "reschedule_without_budget_claim_lost",
                task_id=task_id,
                note="row no longer owned by this claim (watchdog-abandoned handler); not rescheduling",
            )
            return None
        return self.get_task(task_id)

    def mark_dead(
        self,
        task_id: str,
        error_message: str,
        error_class: Optional[str] = None,
        claim_started_at: Optional[str] = None,
    ) -> Optional[Task]:
        """
        Move a task to the Dead Letter Queue (status='dead').

        Use this for fatal errors that will never succeed on retry.

        Args:
            task_id: ID of the task to mark as dead
            error_message: The fatal error description
            error_class: Spec #49 attribution; defaults to 'fatal' since a dead
                task is by definition fatal. Recorded for queue-viewer parity
                with retry/failed rows. Never healed (the healer skips 'dead').
            claim_started_at: Optional lease guard (see ``complete_task``). When
                provided, only kills the row while it is still ``processing``
                under this exact claim timestamp.

        Returns:
            Updated Task object, or None if task not found / claim lost
        """
        now = now_utc()
        guard = " AND status = 'processing' AND started_at = %s" if claim_started_at is not None else ""

        with connect(self.dsn) as conn:
            params: list = [error_message, error_class or "fatal", now, now, task_id]
            if claim_started_at is not None:
                params.append(claim_started_at)
            cursor = conn.execute(
                f"""
                UPDATE tasks
                SET status = 'dead',
                    error_message = %s,
                    error_type = 'fatal',
                    error_class = %s,
                    completed_at = %s,
                    updated_at = %s
                WHERE id = %s{guard}
                """,
                params,
            )

            if cursor.rowcount == 0:
                if claim_started_at is not None:
                    logger.warning(
                        "mark_dead_claim_lost",
                        task_id=task_id,
                        note="row no longer owned by this claim (watchdog-abandoned handler); not killing",
                    )
                else:
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
            sql += " AND stage = ANY(%s)"
            params.append([s.value for s in stage_filter])
        sql += " ORDER BY completed_at DESC LIMIT %s"
        params.append(limit)
        with connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_task(row) for row in rows]

    def retry_dead_task(self, task_id: str) -> Optional[Task]:
        """
        Move a dead or failed task back to pending for manual retry.

        Args:
            task_id: ID of the dead/failed task to retry

        Returns:
            Updated Task object, or None if task not found or not in terminal state
        """
        now = now_utc()

        with connect(self.dsn) as conn:
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
                    updated_at = %s
                WHERE id = %s AND status IN ('dead', 'failed')
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
        cutoff = now_utc() - cooldown
        sql = """
            SELECT * FROM tasks
            WHERE status = 'failed'
              AND error_class = 'infra'
              AND COALESCE(heal_attempts, 0) < %s
              AND (completed_at IS NULL OR completed_at <= %s)
              AND (last_heal_at IS NULL OR last_heal_at <= %s)
            ORDER BY completed_at ASC
            LIMIT %s
        """
        with connect(self.dsn) as conn:
            rows = conn.execute(sql, (max_heal_attempts, cutoff, cutoff, limit)).fetchall()
            return [self._row_to_task(row) for row in rows]

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
        now = now_utc()

        with connect(self.dsn) as conn:
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
                    last_heal_at = %s,
                    updated_at = %s
                WHERE id = %s
                  AND status = 'failed'
                  AND error_class = 'infra'
                  AND COALESCE(heal_attempts, 0) < %s
                """,
                (now, now, task_id, max_heal_attempts),
            )
            rowcount = cursor.rowcount

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
        now = now_utc()

        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    error_message = COALESCE(last_error, 'Retry cancelled by user'),
                    error_class = NULL,
                    next_retry_at = NULL,
                    completed_at = %s,
                    updated_at = %s
                WHERE id = %s AND status = 'retry_scheduled'
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
        now = now_utc()

        with connect(self.dsn) as conn:
            # Get max current priority among pending tasks
            row = conn.execute("SELECT MAX(priority) AS max_priority FROM tasks WHERE status = 'pending'").fetchone()
            max_priority = row["max_priority"] if row and row["max_priority"] is not None else 0

            # Set this task's priority higher
            cursor = conn.execute(
                """
                UPDATE tasks
                SET priority = %s, updated_at = %s
                WHERE id = %s AND status = 'pending'
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
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                DELETE FROM tasks
                WHERE id = %s AND status = 'pending'
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
        with connect(self.dsn) as conn:
            result: Dict[str, List[Task]] = {
                "pending": [],
                "processing": [],
                "retry_scheduled": [],
                "completed": [],
            }

            # Pending tasks
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT 100
                """
            ).fetchall()
            result["pending"] = [self._row_to_task(row) for row in rows]

            # Processing tasks
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'processing'
                ORDER BY started_at ASC
                """
            ).fetchall()
            result["processing"] = [self._row_to_task(row) for row in rows]

            # Retry scheduled tasks
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'retry_scheduled'
                ORDER BY next_retry_at ASC
                LIMIT 50
                """
            ).fetchall()
            result["retry_scheduled"] = [self._row_to_task(row) for row in rows]

            # Recently completed tasks
            rows = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'completed'
                ORDER BY completed_at DESC
                LIMIT %s
                """,
                (include_completed,),
            ).fetchall()
            result["completed"] = [self._row_to_task(row) for row in rows]

            return result

    def sum_duration_by_stage(self, statuses: Optional[List[str]] = None) -> Dict[str, int]:
        """
        Sum the episode audio length (seconds) of queued tasks, grouped by stage.

        Joins ``tasks`` to ``episodes`` so the totals reflect the full backlog,
        not the capped display list returned by :meth:`get_active_tasks` — this is
        what lets the queue viewer show an accurate "time to process" estimate
        even when more than 100 episodes are pending for a stage.

        Dialect note: ``episodes.duration`` is a text column; SQLite's ``SUM``
        coerces each value's leading numeric prefix (non-numeric text counts
        as 0). Postgres has no implicit text→number coercion, so we extract
        the numeric prefix explicitly — same totals, no cast errors on
        malformed values like ``"01:02:03"``.

        Args:
            statuses: Task statuses to include. Defaults to the in-flight
                backlog (``pending`` + ``processing``). Feed-scoped tasks have no
                episode and are naturally excluded by the inner join.

        Returns:
            Mapping of stage value -> total duration in seconds. Stages with no
            durable durations are omitted (caller treats missing as None/0).
        """
        if statuses is None:
            statuses = ["pending", "processing"]
        if not statuses:
            return {}

        with connect(self.dsn) as conn:
            rows = conn.execute(
                r"""
                SELECT t.stage AS stage,
                       SUM(COALESCE(substring(e.duration FROM '^[0-9]+(?:\.[0-9]+)?')::numeric, 0)) AS total
                FROM tasks t
                JOIN episodes e ON t.episode_id = e.id
                WHERE t.status = ANY(%s)
                  AND e.duration IS NOT NULL
                GROUP BY t.stage
                """,
                (list(statuses),),
            ).fetchall()
            return {row["stage"]: int(row["total"]) for row in rows if row["total"] is not None}
