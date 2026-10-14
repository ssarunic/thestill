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
