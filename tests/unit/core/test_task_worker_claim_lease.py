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

"""Claim-lease guard on complete/retry/dead writes.

Closes the watchdog-abandonment race: when the per-stage watchdog gives up on
a wedged handler, that handler's thread keeps running. Its row may since have
been requeued (stale-task reset) and reclaimed by another worker under a fresh
``started_at``. Without a lease guard the revived zombie could complete/kill a
claim it no longer owns and double-fire the successor fan-out. Each claimed
task carries its ``started_at`` as a lease token; complete/retry/dead only
apply while the row is still ``processing`` under that exact token.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from thestill.core.queue_manager import QueueManager, Task, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

EPISODE_ID = "00000000-0000-0000-0000-0000000000e1"


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "lease.db")
    repo = SqlitePodcastRepository(db_path=path)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Lease Test Podcast",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Lease Test Episode",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return path


def _requeue_to_pending(qm: QueueManager, task_id: str) -> None:
    """Simulate the stale-task reset returning a wedged row to the queue."""
    con = sqlite3.connect(qm.db_path)
    con.execute(
        "UPDATE tasks SET status = 'pending', started_at = NULL WHERE id = ?",
        (task_id,),
    )
    con.commit()
    con.close()


class TestCompleteTaskLease:
    def test_stale_claim_cannot_complete_after_reclaim(self, db_path):
        qm = QueueManager(db_path)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)

        first = qm.get_next_task(stage=TaskStage.CLEAN)
        token1 = first.started_at.isoformat()

        # Watchdog abandons the handler; stale reset requeues; another worker
        # reclaims under a fresh started_at.
        _requeue_to_pending(qm, first.id)
        time.sleep(0.005)  # guarantee a distinct claim timestamp
        second = qm.get_next_task(stage=TaskStage.CLEAN)
        token2 = second.started_at.isoformat()
        assert token1 != token2

        # The zombie (token1) must NOT complete the row now owned by token2.
        assert qm.complete_task(first.id, claim_started_at=token1) is False
        assert qm.get_task(first.id).status == TaskStatus.PROCESSING

        # The rightful owner completes.
        assert qm.complete_task(second.id, claim_started_at=token2) is True
        assert qm.get_task(first.id).status == TaskStatus.COMPLETED

    def test_legacy_unguarded_complete_still_works(self, db_path):
        qm = QueueManager(db_path)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        task = qm.get_next_task(stage=TaskStage.CLEAN)
        assert qm.complete_task(task.id) is True
        assert qm.get_task(task.id).status == TaskStatus.COMPLETED


class TestRetryAndDeadLease:
    def test_stale_claim_cannot_reschedule_after_reclaim(self, db_path):
        qm = QueueManager(db_path)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        first = qm.get_next_task(stage=TaskStage.CLEAN)
        token1 = first.started_at.isoformat()

        _requeue_to_pending(qm, first.id)
        time.sleep(0.005)
        second = qm.get_next_task(stage=TaskStage.CLEAN)

        assert qm.schedule_retry(first.id, "zombie error", claim_started_at=token1) is None
        # Row is untouched — still processing under the new owner.
        assert qm.get_task(first.id).status == TaskStatus.PROCESSING
        assert qm.get_task(first.id).retry_count == 0

    def test_stale_claim_cannot_mark_dead_after_reclaim(self, db_path):
        qm = QueueManager(db_path)
        qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.CLEAN)
        first = qm.get_next_task(stage=TaskStage.CLEAN)
        token1 = first.started_at.isoformat()

        _requeue_to_pending(qm, first.id)
        time.sleep(0.005)
        qm.get_next_task(stage=TaskStage.CLEAN)

        assert qm.mark_dead(first.id, "zombie fatal", claim_started_at=token1) is None
        assert qm.get_task(first.id).status == TaskStatus.PROCESSING


class TestWorkerGatesFanOutOnClaim:
    def test_lost_claim_skips_successor_fan_out(self):
        """When complete_task reports the claim was lost, the worker must not
        run supersede / clear / successor enqueue — the double-fan-out guard."""
        queue = MagicMock()
        queue.complete_task.return_value = False  # claim lost
        handler = MagicMock()
        worker = TaskWorker(
            queue_manager=queue,
            task_handlers={TaskStage.CLEAN: handler},
            repository=MagicMock(),
        )
        task = Task(
            id="task-uuid",
            episode_id=EPISODE_ID,
            stage=TaskStage.CLEAN,
            status=TaskStatus.PROCESSING,
            started_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
            metadata={"run_full_pipeline": True},
        )

        worker._process_task(task)

        handler.assert_called_once()  # work ran
        queue.complete_task.assert_called_once()  # completion attempted with the lease
        # ...but no downstream bookkeeping or fan-out happened.
        queue.supersede_stale_tasks.assert_not_called()
        queue.add_task.assert_not_called()
        worker.repository.clear_episode_failure_for_stages.assert_not_called()
