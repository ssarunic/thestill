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

"""Dual-backend contract: the queue never holds two copies of the same work.

An episode whose transcription failed fatally three times had three DLQ rows;
"Retry all" revived every one of them and three transcriptions of the same
episode ran (each with its own clean → summarize → entity chain). These tests
pin the guards on every path that puts a row in line:

- ``add_task`` coalesces into an existing queued (or, for user-chain stages,
  running) task for the same (episode, stage), even under concurrent callers.
- ``retry_dead_tasks`` revives at most one row per (episode, stage), and none
  while the slot already has an active task; ``retry_dead_task`` likewise.
- ``heal_task`` supersedes a failed row whose slot already has an active task.

The Postgres cases are skipped (not failed) when no server is reachable:

    TEST_DATABASE_URL=postgresql://localhost:5432/thestill_queue_test \\
        ./venv/bin/python -m pytest tests/integration/test_queue_no_duplicate_work_contract.py
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

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

PODCAST_ID = "00000000-0000-0000-0000-000000000001"
EPISODE_IDS = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"thestill-dedup-episode-{i}")) for i in range(2)]
EP, EP2 = EPISODE_IDS


def _sqlite_queue(tmp_path: Path) -> QueueManager:
    db_path = str(tmp_path / "dedup.db")
    SqlitePodcastRepository(db_path=db_path).save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Dedup Test Podcast",
            description="",
            episodes=[
                Episode(
                    id=eid,
                    external_id=f"ep-{i}",
                    title=f"Episode {i}",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url=f"https://example.com/ep{i}.mp3",
                    duration=60,
                )
                for i, eid in enumerate(EPISODE_IDS)
            ],
        )
    )
    return QueueManager(db_path)


def _pg_queue():
    import psycopg

    from thestill.core.postgres_queue_manager import PostgresQueueManager
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE tasks, episodes, podcasts CASCADE")
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
            (PODCAST_ID, "https://example.com/feed.xml", "Dedup Test Podcast"),
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


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL"),
        ),
    ]
)
def qm(request, tmp_path):
    if request.param == "sqlite":
        return _sqlite_queue(tmp_path)
    return _pg_queue()


def _active(qm, episode_id: str, stage: TaskStage) -> list:
    live = {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRY_SCHEDULED}
    return [t for t in qm.get_tasks_for_episode(episode_id) if t.stage == stage and t.status in live]


def _dead_task(qm, episode_id: str, stage: TaskStage = TaskStage.TRANSCRIBE) -> str:
    task = qm.add_task(episode_id=episode_id, stage=stage)
    qm.mark_dead(task.id, "fatal: unsupported content type")
    return task.id


def _failed_infra_task(qm, episode_id: str) -> str:
    task = qm.add_task(episode_id=episode_id, stage=TaskStage.TRANSCRIBE)
    for _ in range(task.max_retries):
        qm.schedule_retry(task.id, "Failed to connect", error_class="infra")
    assert qm.get_task(task.id).status == TaskStatus.FAILED
    return task.id


class TestAddTaskCoalesces:
    def test_second_add_while_queued_returns_the_queued_task(self, qm):
        first = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)
        second = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE, metadata={"run_full_pipeline": True})
        assert second.id == first.id
        assert len(_active(qm, EP, TaskStage.TRANSCRIBE)) == 1

    def test_user_stage_add_while_running_returns_the_running_task(self, qm):
        first = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)
        assert qm.get_next_task().id == first.id
        again = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)
        assert again.id == first.id
        assert again.status == TaskStatus.PROCESSING
        assert len(_active(qm, EP, TaskStage.TRANSCRIBE)) == 1

    def test_entity_stage_queues_one_task_behind_a_running_one(self, qm):
        # A running resolve has snapshotted its pending mentions; mentions reset
        # mid-run need one fresh row after it — but only one.
        running = qm.add_task(episode_id=EP, stage=TaskStage.RESOLVE_ENTITIES)
        assert qm.get_next_task().id == running.id
        follow_up = qm.add_task(episode_id=EP, stage=TaskStage.RESOLVE_ENTITIES)
        assert follow_up.id != running.id
        assert qm.add_task(episode_id=EP, stage=TaskStage.RESOLVE_ENTITIES).id == follow_up.id
        assert len(_active(qm, EP, TaskStage.RESOLVE_ENTITIES)) == 2

    def test_add_after_a_dead_task_queues_a_fresh_one(self, qm):
        dead_id = _dead_task(qm, EP)
        fresh = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)
        assert fresh.id != dead_id
        assert fresh.status == TaskStatus.PENDING

    def test_other_episode_and_other_stage_are_independent(self, qm):
        a = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)
        b = qm.add_task(episode_id=EP2, stage=TaskStage.TRANSCRIBE)
        c = qm.add_task(episode_id=EP, stage=TaskStage.CLEAN)
        assert len({a.id, b.id, c.id}) == 3

    def test_concurrent_adds_insert_exactly_one_row(self, qm):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = set(pool.map(lambda _: qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE).id, range(16)))
        assert len(ids) == 1
        assert len(_active(qm, EP, TaskStage.TRANSCRIBE)) == 1


class TestDeadLetterRevival:
    def test_retry_all_revives_one_row_per_episode_and_stage(self, qm):
        # Three fatal attempts on EP leave three DLQ rows; EP2 has one.
        attempts = [_dead_task(qm, EP) for _ in range(3)]
        other = _dead_task(qm, EP2)

        revived = qm.retry_dead_tasks(attempts + [other])

        assert sorted(revived) == sorted([attempts[-1], other])  # newest attempt per slot
        assert [t.id for t in _active(qm, EP, TaskStage.TRANSCRIBE)] == [attempts[-1]]
        assert {qm.get_task(t).status for t in attempts[:2]} == {TaskStatus.DEAD}

    def test_retry_all_skips_a_slot_that_already_has_an_active_task(self, qm):
        dead_id = _dead_task(qm, EP)
        live = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)  # e.g. the episode Retry button

        assert qm.retry_dead_tasks([dead_id]) == []
        assert [t.id for t in _active(qm, EP, TaskStage.TRANSCRIBE)] == [live.id]
        assert qm.get_task(dead_id).status == TaskStatus.DEAD

    def test_retry_all_twice_does_not_add_a_second_row(self, qm):
        attempts = [_dead_task(qm, EP) for _ in range(2)]
        assert len(qm.retry_dead_tasks(attempts)) == 1
        assert qm.retry_dead_tasks(attempts) == []
        assert len(_active(qm, EP, TaskStage.TRANSCRIBE)) == 1

    def test_single_retry_refused_while_the_slot_is_active(self, qm):
        older, newer = _dead_task(qm, EP), _dead_task(qm, EP)
        assert qm.retry_dead_task(newer) is not None
        assert qm.retry_dead_task(older) is None
        assert qm.get_task(older).status == TaskStatus.DEAD
        assert len(_active(qm, EP, TaskStage.TRANSCRIBE)) == 1

    def test_single_retry_still_works_when_the_slot_is_free(self, qm):
        dead_id = _dead_task(qm, EP)
        retried = qm.retry_dead_task(dead_id)
        assert retried is not None and retried.status == TaskStatus.PENDING


class TestHealer:
    def test_heal_supersedes_a_failed_row_whose_slot_is_active(self, qm):
        failed_id = _failed_infra_task(qm, EP)
        live = qm.add_task(episode_id=EP, stage=TaskStage.TRANSCRIBE)

        assert qm.heal_task(failed_id, max_heal_attempts=2) is None
        assert qm.get_task(failed_id).status == TaskStatus.SUPERSEDED
        assert [t.id for t in _active(qm, EP, TaskStage.TRANSCRIBE)] == [live.id]

    def test_heal_still_requeues_when_the_slot_is_free(self, qm):
        failed_id = _failed_infra_task(qm, EP)
        healed = qm.heal_task(failed_id, max_heal_attempts=2)
        assert healed is not None and healed.status == TaskStatus.PENDING


class TestHasActiveTaskForEpisode:
    """Spec #106 — the reprocess guard looks at every stage of one episode."""

    def test_no_tasks_is_inactive(self, qm):
        assert qm.has_active_task_for_episode(EP) is False

    def test_queued_running_and_retry_scheduled_tasks_in_any_stage_count(self, qm):
        qm.add_task(episode_id=EP, stage=TaskStage.REINDEX)
        assert qm.has_active_task_for_episode(EP) is True
        assert qm.has_active_task_for_episode(EP2) is False

        running = qm.get_next_task()
        assert running.stage == TaskStage.REINDEX
        assert qm.has_active_task_for_episode(EP) is True

        qm.schedule_retry(running.id, "timeout")
        assert qm.get_task(running.id).status == TaskStatus.RETRY_SCHEDULED
        assert qm.has_active_task_for_episode(EP) is True

    def test_dead_and_completed_tasks_do_not_count(self, qm):
        _dead_task(qm, EP)
        done = qm.add_task(episode_id=EP, stage=TaskStage.CLEAN)
        qm.get_next_task()
        qm.complete_task(done.id)
        assert qm.has_active_task_for_episode(EP) is False


def _hold_queue_write_lock(qm, episode_id: str):
    """Open a transaction that holds the lock the guarded writes take.

    SQLite: ``BEGIN IMMEDIATE`` (the database writer lock). Postgres: the
    episode's advisory lock. Returns ``(conn, release)``; ``conn`` can write
    inside the held transaction, ``release()`` commits it.
    """
    if isinstance(qm, QueueManager):
        import sqlite3

        conn = sqlite3.connect(str(qm.db_path), isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")

        def release() -> None:
            conn.execute("COMMIT")
            conn.close()

        return conn, release

    import psycopg

    from thestill.core.postgres_queue_manager import _target_lock_key

    conn = psycopg.connect(PG_DSN)
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (_target_lock_key(episode_id),))

    def release() -> None:
        conn.commit()
        conn.close()

    return conn, release


def _insert_pending_row(conn, qm, episode_id: str, stage: TaskStage) -> None:
    """Insert a pending task on a held-lock connection (raw SQL, no guard)."""
    now = datetime.now(timezone.utc)
    if isinstance(qm, QueueManager):
        conn.execute(
            "INSERT INTO tasks (id, episode_id, stage, status, priority, created_at, updated_at)"
            " VALUES (?, ?, ?, 'pending', 0, ?, ?)",
            (str(uuid.uuid4()), episode_id, stage.value, now.isoformat(), now.isoformat()),
        )
    else:
        conn.execute(
            "INSERT INTO tasks (id, episode_id, stage, status, priority, created_at, updated_at)"
            " VALUES (%s, %s, %s, 'pending', 0, %s, %s)",
            (str(uuid.uuid4()), episode_id, stage.value, now, now),
        )


def _all_active(qm, episode_id: str) -> list:
    live = {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.RETRY_SCHEDULED}
    return [t for t in qm.get_tasks_for_episode(episode_id) if t.status in live]


class TestAddTaskIfEpisodeIdle:
    """Spec #106 review — the reprocess guard is one check-and-insert."""

    def test_idle_episode_gets_the_task_with_its_metadata(self, qm):
        task = qm.add_task_if_episode_idle(EP, TaskStage.CLEAN, metadata={"reprocess": {"from_stage": "clean"}})
        assert task is not None and task.status == TaskStatus.PENDING
        assert qm.get_task(task.id).metadata == {"reprocess": {"from_stage": "clean"}}

    def test_an_active_task_in_any_stage_makes_it_busy(self, qm):
        live = qm.add_task(episode_id=EP, stage=TaskStage.REINDEX)
        assert qm.add_task_if_episode_idle(EP, TaskStage.CLEAN) is None
        assert [t.id for t in _all_active(qm, EP)] == [live.id]
        # Another episode is unaffected.
        assert qm.add_task_if_episode_idle(EP2, TaskStage.CLEAN) is not None

    def test_second_same_stage_request_is_busy_not_coalesced(self, qm):
        first = qm.add_task_if_episode_idle(EP, TaskStage.CLEAN, metadata={"reprocess": {"refresh": False}})
        second = qm.add_task_if_episode_idle(EP, TaskStage.CLEAN, metadata={"reprocess": {"refresh": True}})
        assert first is not None
        assert second is None  # reported busy; its options are not silently dropped
        [active] = _all_active(qm, EP)
        assert active.id == first.id
        assert active.metadata == {"reprocess": {"refresh": False}}

    def test_concurrent_requests_for_different_stages_queue_exactly_one(self, qm):
        stages = [TaskStage.CLEAN, TaskStage.SUMMARIZE] * 4
        with ThreadPoolExecutor(max_workers=len(stages)) as pool:
            results = list(pool.map(lambda stage: qm.add_task_if_episode_idle(EP, stage), stages))
        queued = [r for r in results if r is not None]
        assert len(queued) == 1
        assert results.count(None) == len(stages) - 1
        assert [t.id for t in _all_active(qm, EP)] == [queued[0].id]

    def test_the_check_runs_inside_the_write_lock(self, qm):
        # Another writer holds the lock and inserts a task for the episode in
        # a different stage. The guarded call must wait for it and then see
        # that row; a check outside the lock would have passed and inserted.
        conn, release = _hold_queue_write_lock(qm, EP)
        try:
            _insert_pending_row(conn, qm, EP, TaskStage.SUMMARIZE)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(qm.add_task_if_episode_idle, EP, TaskStage.CLEAN)
                time.sleep(0.3)
                assert not pending.done()  # blocked on the lock
                release()
                release = None
                assert pending.result(timeout=10) is None
        finally:
            if release is not None:
                release()
        assert [t.stage for t in _all_active(qm, EP)] == [TaskStage.SUMMARIZE]


class TestCompleteTaskHandOff:
    """Spec #106 review — a stage and its successor are one transaction."""

    def test_successor_inherits_priority_and_metadata(self, qm):
        meta = {"run_full_pipeline": True, "reprocess": {"from_stage": "clean"}}
        task = qm.add_task(episode_id=EP, stage=TaskStage.CLEAN, priority=7, metadata=meta)
        claimed = qm.get_next_task()
        assert qm.complete_task(
            claimed.id, claim_started_at=claimed.started_at.isoformat(), enqueue_next=[TaskStage.SUMMARIZE]
        )
        assert qm.get_task(task.id).status == TaskStatus.COMPLETED
        [successor] = _all_active(qm, EP)
        assert successor.stage == TaskStage.SUMMARIZE
        assert successor.priority == 7
        assert successor.metadata == meta

    def test_lost_claim_queues_no_successor(self, qm):
        qm.add_task(episode_id=EP, stage=TaskStage.CLEAN, metadata={"run_full_pipeline": True})
        claimed = qm.get_next_task()
        assert not qm.complete_task(
            claimed.id, claim_started_at="1999-01-01T00:00:00+00:00", enqueue_next=[TaskStage.SUMMARIZE]
        )
        assert [t.id for t in _all_active(qm, EP)] == [claimed.id]

    def test_successor_coalesces_into_a_queued_copy(self, qm):
        qm.add_task(episode_id=EP, stage=TaskStage.CLEAN, metadata={"run_full_pipeline": True})
        claimed = qm.get_next_task()
        queued = qm.add_task(episode_id=EP, stage=TaskStage.SUMMARIZE)
        assert qm.complete_task(claimed.id, enqueue_next=[TaskStage.SUMMARIZE])
        assert [t.id for t in _all_active(qm, EP)] == [queued.id]

    def test_reprocess_racing_the_hand_off_is_always_busy(self, qm):
        # Both writers wait on the same lock; whichever runs first, the guard
        # sees either the running stage or its successor, never a gap.
        qm.add_task(episode_id=EP, stage=TaskStage.CLEAN, metadata={"run_full_pipeline": True})
        claimed = qm.get_next_task()
        conn, release = _hold_queue_write_lock(qm, EP)
        with ThreadPoolExecutor(max_workers=2) as pool:
            hand_off = pool.submit(qm.complete_task, claimed.id, None, [TaskStage.SUMMARIZE])
            reprocess = pool.submit(qm.add_task_if_episode_idle, EP, TaskStage.CLEAN)
            time.sleep(0.2)
            release()
            assert hand_off.result(timeout=10) is True
            assert reprocess.result(timeout=10) is None
        assert [t.stage for t in _all_active(qm, EP)] == [TaskStage.SUMMARIZE]
