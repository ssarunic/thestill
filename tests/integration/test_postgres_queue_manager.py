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

"""Integration tests for ``PostgresQueueManager`` (spec #44 Phase 3).

PG-only (the SQLite queue has its own unit suite); skipped when
``TEST_DATABASE_URL`` is unset or unreachable. Mirrors the key behavioural
tests from ``tests/unit/core/test_queue_auto_heal.py`` and
``tests/unit/core/test_task_worker_claim_lease.py``, plus a concurrency smoke
for the single-statement ``FOR UPDATE SKIP LOCKED`` claim:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_queue \\
        ./venv/bin/python -m pytest tests/integration/test_postgres_queue_manager.py
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from thestill.core.queue_manager import TaskStage, TaskStatus
from thestill.utils.datetime_utils import now_utc

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)

pytestmark = pytest.mark.skipif(
    not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL to run the queue tests"
)

PODCAST_ID = "00000000-0000-0000-0000-000000000001"
# 60 episodes: E1..E60 as 00000000-0000-0000-0000-0000000000{01..60} hex-ish;
# generate deterministic UUIDs instead to keep them valid.
EPISODE_IDS = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"thestill-test-episode-{i}")) for i in range(60)]
EPISODE_ID = EPISODE_IDS[0]
EPISODE_ID_2 = EPISODE_IDS[1]


@pytest.fixture(scope="session")
def _schema():
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)


@pytest.fixture
def qm(_schema):
    """Clean tasks table + one parent podcast and its episodes for FKs."""
    import psycopg

    from thestill.core.postgres_queue_manager import PostgresQueueManager

    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE tasks, episodes, podcasts CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
            (PODCAST_ID, "https://example.com/feed.xml", "Queue Test Podcast"),
        )
        for i, eid in enumerate(EPISODE_IDS):
            conn.execute(
                """
                INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, duration)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (eid, PODCAST_ID, f"ep-{i}", f"Episode {i}", f"https://example.com/ep{i}.mp3", "60"),
            )
    return PostgresQueueManager(PG_DSN)


def _exec(sql: str, params: tuple) -> None:
    import psycopg

    with psycopg.connect(PG_DSN) as conn:
        conn.execute(sql, params)


def _requeue_to_pending(task_id: str) -> None:
    """Simulate the stale-task reset returning a wedged row to the queue."""
    _exec("UPDATE tasks SET status = 'pending', started_at = NULL WHERE id = %s", (task_id,))


def _backdate_completed(task_id: str, age_minutes: float) -> None:
    stamp = now_utc() - timedelta(minutes=age_minutes)
    _exec("UPDATE tasks SET completed_at = %s WHERE id = %s", (stamp, task_id))


def _backdate_next_retry(task_id: str, age_minutes: float) -> None:
    stamp = now_utc() - timedelta(minutes=age_minutes)
    _exec("UPDATE tasks SET next_retry_at = %s WHERE id = %s", (stamp, task_id))


def _make_failed_task(
    qm,
    error_class: str,
    *,
    error_message: str = "Failed to connect: [Errno 8] nodename nor servname provided",
    completed_age_minutes: float = 60.0,
) -> str:
    """Create a ``failed`` task carrying ``error_class``, aged for cooldown.

    Drives ``schedule_retry`` to exhaustion (the real path that lands a row
    in ``failed``), then back-dates ``completed_at`` so cooldown windows can
    be exercised deterministically.
    """
    task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
    res = None
    for _ in range(task.max_retries):
        res = qm.schedule_retry(task.id, error_message, error_class=error_class)
    assert res is not None and res.status == TaskStatus.FAILED
    _backdate_completed(task.id, completed_age_minutes)
    return task.id


# ---------------------------------------------------------------------------
# add / claim / complete round trip
# ---------------------------------------------------------------------------
class TestAddClaimComplete:
    def test_round_trip_with_metadata_fidelity(self, qm):
        meta = {"run_full_pipeline": True, "initiated_by": "test", "nested": {"a": 1, "b": [1, 2]}}
        created = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD, priority=5, metadata=meta)
        assert created.status == TaskStatus.PENDING
        assert created.metadata == meta

        claimed = qm.get_next_task()
        assert claimed is not None
        assert claimed.id == created.id
        assert claimed.episode_id == EPISODE_ID
        assert claimed.podcast_id is None
        assert claimed.status == TaskStatus.PROCESSING
        assert claimed.started_at is not None
        assert claimed.stage == TaskStage.DOWNLOAD
        assert claimed.priority == 5
        assert claimed.metadata == meta  # jsonb round-trips the dict exactly
        assert isinstance(claimed.created_at, datetime)
        assert claimed.created_at.tzinfo is not None

        assert qm.complete_task(claimed.id) is True
        done = qm.get_task(claimed.id)
        assert done.status == TaskStatus.COMPLETED
        assert done.completed_at is not None
        assert done.metadata == meta

    def test_empty_metadata_reads_back_as_empty_dict(self, qm):
        created = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD)
        assert qm.get_task(created.id).metadata == {}

    def test_claim_order_priority_desc_then_created_at_asc(self, qm):
        low_first = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD, priority=0)
        high = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD, priority=10)
        low_second = qm.add_task(episode_id=EPISODE_IDS[2], stage=TaskStage.DOWNLOAD, priority=0)

        assert qm.get_next_task().id == high.id
        assert qm.get_next_task().id == low_first.id  # FIFO among equal priority
        assert qm.get_next_task().id == low_second.id
        assert qm.get_next_task() is None

    def test_stage_filter(self, qm):
        qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        clean = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.CLEAN)
        got = qm.get_next_task(stage=TaskStage.CLEAN)
        assert got.id == clean.id
        assert qm.get_next_task(stage=TaskStage.CLEAN) is None


# ---------------------------------------------------------------------------
# exclusion filters (episode/podcast) and the feed-task NULL arm
# ---------------------------------------------------------------------------
class TestClaimExclusions:
    def test_exclude_episode_ids_skips_those_episodes(self, qm):
        t1 = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        t2 = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD)
        got = qm.get_next_task(exclude_episode_ids={EPISODE_IDS[0]})
        assert got.id == t2.id
        # Everything eligible is excluded now.
        assert qm.get_next_task(exclude_episode_ids={EPISODE_IDS[0], EPISODE_IDS[1]}) is None
        assert qm.get_task(t1.id).status == TaskStatus.PENDING

    def test_feed_task_claimable_through_episode_exclusions(self, qm):
        # Spec #48 NULL arm: a feed task (episode_id NULL) must not be filtered
        # out by the episode-exclusion set.
        feed = qm.add_feed_task(PODCAST_ID)
        assert feed is not None
        got = qm.get_next_task(exclude_episode_ids={EPISODE_IDS[0], EPISODE_IDS[1]})
        assert got is not None
        assert got.id == feed.id
        assert got.podcast_id == PODCAST_ID
        assert got.episode_id is None

    def test_exclude_podcast_ids_is_per_podcast_mutex(self, qm):
        feed = qm.add_feed_task(PODCAST_ID)
        assert feed is not None
        assert qm.get_next_task(exclude_podcast_ids={PODCAST_ID}) is None
        # Episode tasks (podcast_id NULL) pass through the podcast exclusion.
        ep = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        got = qm.get_next_task(exclude_podcast_ids={PODCAST_ID})
        assert got.id == ep.id


# ---------------------------------------------------------------------------
# feed-task uniqueness guard
# ---------------------------------------------------------------------------
class TestFeedTaskCoalescing:
    def test_second_enqueue_coalesces(self, qm):
        assert qm.add_feed_task(PODCAST_ID) is not None
        assert qm.add_feed_task(PODCAST_ID) is None
        assert qm.has_pending_feed_task(PODCAST_ID) is True

    def test_rejects_episode_scoped_stage(self, qm):
        with pytest.raises(ValueError):
            qm.add_feed_task(PODCAST_ID, stage=TaskStage.DOWNLOAD)


# ---------------------------------------------------------------------------
# retry scheduling
# ---------------------------------------------------------------------------
class TestRetryScheduling:
    def test_retry_scheduled_not_claimable_until_next_retry_at(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        claimed = qm.get_next_task()
        assert claimed.id == task.id

        res = qm.schedule_retry(task.id, "transient error", error_class="infra")
        assert res.status == TaskStatus.RETRY_SCHEDULED
        assert res.retry_count == 1
        assert res.next_retry_at is not None
        assert res.started_at is None
        assert res.error_class == "infra"

        # Backoff timer still in the future → not claimable.
        assert qm.get_next_task() is None

        # Once next_retry_at has passed, the row becomes claimable again.
        _backdate_next_retry(task.id, 1)
        reclaimed = qm.get_next_task()
        assert reclaimed is not None
        assert reclaimed.id == task.id
        assert reclaimed.retry_count == 1
        assert reclaimed.status == TaskStatus.PROCESSING

    def test_budget_exhaustion_marks_failed_with_error_class(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        res = None
        for _ in range(task.max_retries):
            res = qm.schedule_retry(task.id, "still down", error_class="infra")
        assert res.status == TaskStatus.FAILED
        assert res.retry_count == task.max_retries
        assert res.error_message == "still down"
        assert res.last_error == "still down"
        assert res.error_type is not None and res.error_type.value == "transient"
        assert res.error_class == "infra"
        assert res.completed_at is not None

    def test_schedule_retry_none_error_class_preserves_prior_label(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.schedule_retry(task.id, "boom", error_class="infra")
        res = qm.schedule_retry(task.id, "boom again")  # legacy caller, no class
        assert res.error_class == "infra"  # COALESCE keeps the prior label

    def test_schedule_retry_unknown_task_returns_none(self, qm):
        assert qm.schedule_retry(str(uuid.uuid4()), "nope") is None

    def test_reschedule_without_budget_keeps_retry_count(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.get_next_task()
        qm.schedule_retry(task.id, "first failure", error_class="infra")
        assert qm.get_task(task.id).retry_count == 1

        _backdate_next_retry(task.id, 1)
        qm.get_next_task()  # reclaim (processing)

        res = qm.reschedule_without_budget(task.id, "breaker open", error_class="infra")
        assert res.status == TaskStatus.RETRY_SCHEDULED
        assert res.retry_count == 1  # budget NOT charged
        assert res.started_at is None
        assert res.error_class == "infra"
        # next_retry_at = now → immediately claimable again.
        assert res.next_retry_at <= now_utc()
        assert qm.get_next_task().id == task.id


# ---------------------------------------------------------------------------
# lease guards (spec #49 follow-up — watchdog-abandonment race)
# ---------------------------------------------------------------------------
class TestClaimLeaseGuards:
    def _claim_requeue_reclaim(self, qm) -> tuple[str, str, str]:
        """Claim, simulate stale-reset requeue, reclaim under a new token."""
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        first = qm.get_next_task(stage=TaskStage.CLEAN)
        token1 = first.started_at.isoformat()

        _requeue_to_pending(first.id)
        time.sleep(0.005)  # guarantee a distinct claim timestamp
        second = qm.get_next_task(stage=TaskStage.CLEAN)
        token2 = second.started_at.isoformat()
        assert token1 != token2
        return first.id, token1, token2

    def test_stale_claim_cannot_complete_after_reclaim(self, qm):
        task_id, token1, token2 = self._claim_requeue_reclaim(qm)

        # The zombie (token1) must NOT complete the row now owned by token2.
        assert qm.complete_task(task_id, claim_started_at=token1) is False
        assert qm.get_task(task_id).status == TaskStatus.PROCESSING

        # The rightful owner completes.
        assert qm.complete_task(task_id, claim_started_at=token2) is True
        assert qm.get_task(task_id).status == TaskStatus.COMPLETED

    def test_legacy_unguarded_complete_still_works(self, qm):
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        task = qm.get_next_task(stage=TaskStage.CLEAN)
        assert qm.complete_task(task.id) is True
        assert qm.get_task(task.id).status == TaskStatus.COMPLETED

    def test_stale_claim_cannot_reschedule_after_reclaim(self, qm):
        task_id, token1, _ = self._claim_requeue_reclaim(qm)

        assert qm.schedule_retry(task_id, "zombie error", claim_started_at=token1) is None
        # Row is untouched — still processing under the new owner.
        assert qm.get_task(task_id).status == TaskStatus.PROCESSING
        assert qm.get_task(task_id).retry_count == 0

    def test_stale_claim_cannot_reschedule_without_budget_after_reclaim(self, qm):
        task_id, token1, _ = self._claim_requeue_reclaim(qm)

        assert qm.reschedule_without_budget(task_id, "zombie breaker", claim_started_at=token1) is None
        assert qm.get_task(task_id).status == TaskStatus.PROCESSING

    def test_stale_claim_cannot_mark_dead_after_reclaim(self, qm):
        task_id, token1, _ = self._claim_requeue_reclaim(qm)

        assert qm.mark_dead(task_id, "zombie fatal", claim_started_at=token1) is None
        assert qm.get_task(task_id).status == TaskStatus.PROCESSING

    def test_valid_token_operations_succeed(self, qm):
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        task = qm.get_next_task(stage=TaskStage.CLEAN)
        token = task.started_at.isoformat()
        res = qm.schedule_retry(task.id, "real error", error_class="item", claim_started_at=token)
        assert res is not None
        assert res.status == TaskStatus.RETRY_SCHEDULED


# ---------------------------------------------------------------------------
# heal path (spec #49 L3)
# ---------------------------------------------------------------------------
class TestHealPath:
    def test_find_healable_selects_infra_failed_past_cooldown(self, qm):
        task_id = _make_failed_task(qm, "infra", completed_age_minutes=60)
        healable = qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2)
        assert [t.id for t in healable] == [task_id]

    def test_find_healable_excludes_item_class(self, qm):
        _make_failed_task(qm, "item", completed_age_minutes=60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_find_healable_excludes_recent_failures_within_cooldown(self, qm):
        _make_failed_task(qm, "infra", completed_age_minutes=2)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_find_healable_excludes_dead_tasks(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        # A dead row carrying error_class='infra' must still never be healed.
        qm.mark_dead(task.id, "boom", error_class="infra")
        _backdate_completed(task.id, 60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_find_healable_excludes_tasks_at_heal_cap(self, qm):
        task_id = _make_failed_task(qm, "infra", completed_age_minutes=60)
        # Heal once (attempts -> 1), let it re-fail, heal again (-> 2 == cap).
        qm.heal_task(task_id, 2)
        for _ in range(qm.get_task(task_id).max_retries):
            qm.schedule_retry(task_id, "still down", error_class="infra")
        _backdate_completed(task_id, 60)
        qm.heal_task(task_id, 2)
        for _ in range(qm.get_task(task_id).max_retries):
            qm.schedule_retry(task_id, "still down", error_class="infra")
        _backdate_completed(task_id, 60)
        assert qm.get_task(task_id).heal_attempts == 2
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_heal_task_requeues_and_resets_budget(self, qm):
        task_id = _make_failed_task(qm, "infra")
        assert qm.get_task(task_id).retry_count == qm.get_task(task_id).max_retries

        healed = qm.heal_task(task_id, 2)
        assert healed is not None
        assert healed.status == TaskStatus.PENDING
        assert healed.retry_count == 0
        assert healed.heal_attempts == 1
        assert healed.last_heal_at is not None
        assert healed.last_error is None
        assert healed.error_class == "infra"  # label kept for the next round

    def test_heal_task_refuses_dead(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(task.id, "boom", error_class="infra")
        assert qm.heal_task(task.id, 2) is None
        assert qm.get_task(task.id).status == TaskStatus.DEAD

    def test_heal_task_refuses_over_cap(self, qm):
        task_id = _make_failed_task(qm, "infra")
        assert qm.heal_task(task_id, 0) is None
        assert qm.get_task(task_id).status == TaskStatus.FAILED

    def test_heal_task_refuses_item_class(self, qm):
        task_id = _make_failed_task(qm, "item")
        assert qm.heal_task(task_id, 2) is None

    def test_fail_task_clears_infra_class_so_healer_skips(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.schedule_retry(task.id, "Failed to connect: [Errno 8] nodename", error_class="infra")
        assert qm.get_task(task.id).error_class == "infra"

        qm.fail_task(task.id, "Pipeline cancelled by user")
        cancelled = qm.get_task(task.id)
        assert cancelled.status == TaskStatus.FAILED
        assert cancelled.error_class is None

        _backdate_completed(task.id, 60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_cancel_retry_clears_infra_class_so_healer_skips(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.schedule_retry(task.id, "Failed to connect: [Errno 8] nodename", error_class="infra")
        assert qm.get_task(task.id).status == TaskStatus.RETRY_SCHEDULED

        cancelled = qm.cancel_retry(task.id)
        assert cancelled is not None
        assert cancelled.status == TaskStatus.FAILED
        assert cancelled.error_class is None
        assert cancelled.next_retry_at is None

        _backdate_completed(task.id, 60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_mark_dead_defaults_to_fatal_class(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNSAMPLE)
        qm.mark_dead(task.id, "corrupt audio")
        got = qm.get_task(task.id)
        assert got.status == TaskStatus.DEAD
        assert got.error_class == "fatal"


# ---------------------------------------------------------------------------
# stale reset / interrupted recovery
# ---------------------------------------------------------------------------
class TestStaleAndRecovery:
    def test_reset_stale_tasks_reclaims_old_processing_rows(self, qm):
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD)
        claimed = qm.get_next_task()
        assert claimed.id == task.id
        # A fresh claim is NOT stale.
        assert qm.reset_stale_tasks(timeout_minutes=30) == 0

        _exec(
            "UPDATE tasks SET started_at = %s WHERE id = %s",
            (now_utc() - timedelta(minutes=60), task.id),
        )
        assert qm.reset_stale_tasks(timeout_minutes=30) == 1
        got = qm.get_task(task.id)
        assert got.status == TaskStatus.PENDING
        assert got.started_at is None

    def test_recover_interrupted_resumes_idempotent_fails_entity_branch(self, qm):
        user_task = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        entity_task = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.REINDEX)
        excluded_task = qm.add_task(episode_id=EPISODE_IDS[2], stage=TaskStage.TRANSCRIBE)
        for _ in range(3):
            assert qm.get_next_task() is not None

        recovered = qm.recover_interrupted_tasks(excluded_stages=[TaskStage.TRANSCRIBE])
        assert recovered == 2
        assert qm.get_task(user_task.id).status == TaskStatus.PENDING
        assert qm.get_task(entity_task.id).status == TaskStatus.FAILED
        assert qm.get_task(entity_task.id).error_message == "Task interrupted by server restart"
        # Restart-failed rows are infra-class so the healer loop requeues them.
        assert qm.get_task(entity_task.id).error_class == "infra"
        # Excluded stage left untouched (remote job may still be running).
        assert qm.get_task(excluded_task.id).status == TaskStatus.PROCESSING


# ---------------------------------------------------------------------------
# bookkeeping helpers
# ---------------------------------------------------------------------------
class TestBookkeeping:
    def test_queue_stats_and_pending_count(self, qm):
        qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD)
        claimed = qm.get_next_task()
        qm.complete_task(claimed.id)
        stats = qm.get_queue_stats()
        assert stats["pending"] == 1
        assert stats["completed"] == 1
        assert stats["dead"] == 0
        assert qm.get_pending_count() == 1

    def test_supersede_stale_tasks_same_branch_only(self, qm):
        # Dead transcribe row + dead reindex row for the same episode.
        t_user = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(t_user.id, "boom")
        t_entity = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.REINDEX)
        qm.mark_dead(t_entity.id, "boom")

        # Summarize completing supersedes the user-chain row only.
        assert qm.supersede_stale_tasks(EPISODE_ID, TaskStage.SUMMARIZE) == 1
        assert qm.get_task(t_user.id).status == TaskStatus.SUPERSEDED
        assert qm.get_task(t_entity.id).status == TaskStatus.DEAD

    def test_claim_pending_for_coalescing(self, qm):
        qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.REBUILD_COOCCURRENCES)
        qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.REBUILD_COOCCURRENCES)
        processing = qm.add_task(episode_id=EPISODE_IDS[2], stage=TaskStage.REBUILD_COOCCURRENCES)
        qm.get_next_task(exclude_episode_ids={EPISODE_IDS[0], EPISODE_IDS[1]})  # claims [2]

        claimed = qm.claim_pending_for_coalescing(TaskStage.REBUILD_COOCCURRENCES)
        assert sorted(claimed) == sorted([EPISODE_IDS[0], EPISODE_IDS[1]])
        # Peer worker's processing row untouched.
        assert qm.get_task(processing.id).status == TaskStatus.PROCESSING

    def test_bump_cancel_and_dead_listing(self, qm):
        t1 = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD, priority=5)
        t2 = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD, priority=0)
        assert qm.bump_task(t2.id) is True
        assert qm.get_next_task().id == t2.id  # bumped above priority 5

        assert qm.cancel_task(t1.id) is True
        assert qm.get_task(t1.id) is None

        dead = qm.add_task(episode_id=EPISODE_IDS[2], stage=TaskStage.CLEAN)
        qm.mark_dead(dead.id, "fatal")
        listed = qm.get_dead_tasks()
        assert [t.id for t in listed] == [dead.id]
        assert qm.get_dead_tasks(stage_filter=[TaskStage.DOWNLOAD]) == []

        retried = qm.retry_dead_task(dead.id)
        assert retried.status == TaskStatus.PENDING
        assert retried.retry_count == 0
        assert retried.error_message is None

    def test_cleanup_old_tasks(self, qm):
        old = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        qm.get_next_task()
        qm.complete_task(old.id)
        _backdate_completed(old.id, 60 * 24 * 10)  # 10 days old
        fresh = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD)
        qm.get_next_task()
        qm.complete_task(fresh.id)

        assert qm.cleanup_old_tasks(days=7) == 1
        assert qm.get_task(old.id) is None
        assert qm.get_task(fresh.id) is not None

    def test_get_active_tasks_and_sum_duration(self, qm):
        pend = qm.add_task(episode_id=EPISODE_IDS[0], stage=TaskStage.DOWNLOAD)
        proc = qm.add_task(episode_id=EPISODE_IDS[1], stage=TaskStage.DOWNLOAD)
        qm.get_next_task(exclude_episode_ids={EPISODE_IDS[0]})  # claims proc

        active = qm.get_active_tasks()
        assert [t.id for t in active["pending"]] == [pend.id]
        assert [t.id for t in active["processing"]] == [proc.id]

        # Two download tasks × 60s episode duration.
        totals = qm.sum_duration_by_stage()
        assert totals == {"download": 120}


# ---------------------------------------------------------------------------
# concurrency smoke — the whole point of Phase 3
# ---------------------------------------------------------------------------
class TestConcurrencySmoke:
    def test_eight_workers_claim_fifty_tasks_exactly_once(self, qm):
        n_tasks = 50
        task_ids = set()
        for i in range(n_tasks):
            task_ids.add(qm.add_task(episode_id=EPISODE_IDS[i], stage=TaskStage.DOWNLOAD).id)

        def worker() -> list[str]:
            from thestill.core.postgres_queue_manager import PostgresQueueManager

            own = PostgresQueueManager(PG_DSN)  # each thread its own manager
            claimed: list[str] = []
            while True:
                task = own.get_next_task()
                if task is None:
                    break
                claimed.append(task.id)
            return claimed

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [f.result() for f in [pool.submit(worker) for _ in range(8)]]

        all_claims = [tid for claims in results for tid in claims]
        # Every task claimed exactly once: no double-claims, no misses.
        assert len(all_claims) == n_tasks
        assert set(all_claims) == task_ids
        # And every row ended up processing.
        assert qm.get_queue_stats()["processing"] == n_tasks
