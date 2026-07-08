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

"""Spec #49 Layer 4 — idempotent restart recovery.

``recover_interrupted_tasks`` splits interrupted ``processing`` rows by stage
idempotency: the user chain (download→summarize) + REFRESH_FEED RESUME to
``pending``; the entity branch stays the conservative ``failed`` but is stamped
``error_class='infra'`` so the L3 healer loop requeues it after its cooldown;
explicitly excluded stages are left untouched in ``processing``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus, is_idempotent_stage
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PODCAST_ID = "00000000-0000-0000-0000-000000000001"
EPISODE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def qm(tmp_path: Path) -> QueueManager:
    db = str(tmp_path / "restart.db")
    repo = SqlitePodcastRepository(db_path=db)
    repo.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Restart Test",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Restart Episode",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return QueueManager(db)


def _mark_all_processing(qm: QueueManager) -> None:
    con = sqlite3.connect(qm.db_path)
    con.execute("UPDATE tasks SET status='processing'")
    con.commit()
    con.close()


def _status_by_stage(qm: QueueManager) -> dict[str, str]:
    con = sqlite3.connect(qm.db_path)
    rows = con.execute("SELECT stage, status FROM tasks").fetchall()
    con.close()
    return {stage: status for stage, status in rows}


class TestIdempotentPredicate:
    @pytest.mark.parametrize(
        "stage",
        [
            TaskStage.DOWNLOAD,
            TaskStage.DOWNSAMPLE,
            TaskStage.TRANSCRIBE,
            TaskStage.CLEAN,
            TaskStage.SUMMARIZE,
            TaskStage.REFRESH_FEED,
        ],
    )
    def test_user_chain_and_feed_are_idempotent(self, stage):
        assert is_idempotent_stage(stage) is True

    @pytest.mark.parametrize(
        "stage",
        [
            TaskStage.EXTRACT_ENTITIES,
            TaskStage.RESOLVE_ENTITIES,
            TaskStage.REINDEX,
            TaskStage.ENRICH_ENTITIES,
        ],
    )
    def test_entity_branch_is_not_idempotent(self, stage):
        assert is_idempotent_stage(stage) is False


class TestRecoverInterruptedTasks:
    def test_idempotent_resumes_entity_fails(self, qm):
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.EXTRACT_ENTITIES)
        qm.add_feed_task(PODCAST_ID)
        _mark_all_processing(qm)

        recovered = qm.recover_interrupted_tasks()

        statuses = _status_by_stage(qm)
        assert statuses["download"] == "pending"
        assert statuses["transcribe"] == "pending"
        assert statuses["refresh-feed"] == "pending"
        assert statuses["extract-entities"] == "failed"
        assert recovered == 4  # 3 resumed + 1 failed

    def test_excluded_stage_left_processing(self, qm):
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        _mark_all_processing(qm)

        # Cloud transcribe: exclude it — its remote job may still be running.
        qm.recover_interrupted_tasks(excluded_stages=[TaskStage.TRANSCRIBE])

        statuses = _status_by_stage(qm)
        assert statuses["download"] == "pending"  # idempotent → resumed
        assert statuses["transcribe"] == "processing"  # excluded → untouched

    def test_excluded_overrides_idempotent_no_fail(self, qm):
        # An excluded idempotent stage must NOT be marked failed either.
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        _mark_all_processing(qm)

        qm.recover_interrupted_tasks(excluded_stages=[TaskStage.TRANSCRIBE])

        assert _status_by_stage(qm)["transcribe"] == "processing"

    def test_entity_fail_is_stamped_infra_and_healable(self, qm):
        # A restart is a shared-infrastructure event: the entity-branch row it
        # fails must carry error_class='infra' so the healer loop requeues it
        # instead of stranding it in the DLQ (Retries 0/3, forever).
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.COMPUTE_RELATED)
        _mark_all_processing(qm)

        qm.recover_interrupted_tasks()

        con = sqlite3.connect(qm.db_path)
        row = con.execute("SELECT status, error_class FROM tasks WHERE stage='compute-related'").fetchone()
        con.close()
        assert row == ("failed", "infra")

        healable = qm.find_healable_tasks(cooldown=timedelta(seconds=0), max_heal_attempts=3)
        assert [t.stage for t in healable] == [TaskStage.COMPUTE_RELATED]

        healed = qm.heal_task(healable[0].id, max_heal_attempts=3)
        assert healed is not None
        assert healed.status == TaskStatus.PENDING

    def test_resumed_task_is_dequeued_again(self, qm):
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNLOAD)
        _mark_all_processing(qm)
        qm.recover_interrupted_tasks()

        # A resumed task is pending and flows back through the worker.
        nxt = qm.get_next_task(stage=TaskStage.DOWNLOAD)
        assert nxt is not None
        assert nxt.status == TaskStatus.PROCESSING  # get_next_task claims it
