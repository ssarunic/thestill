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

"""Spec #49 — queue auto-healing: error attribution + the healer loop.

Covers the durable behaviour that makes an infrastructure outage
self-resolve once the dependency recovers, instead of leaving a pile of
terminally-``failed`` tasks for a human to retry by hand:

- ``schedule_retry`` persists the infra/item ``error_class`` onto the row.
- ``find_healable_tasks`` selects ONLY ``failed`` + ``error_class='infra'``
  rows past their cooldown and under the heal cap — never ``dead``, never
  ``item``.
- ``heal_task`` requeues an infra failure (fresh retry budget), increments
  ``heal_attempts``, and refuses ``dead`` / over-cap rows.
- ``TaskWorker._heal_terminal_tasks`` drains a sweep end-to-end.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.datetime_utils import now_utc

EPISODE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """SQLite DB with one podcast + episode so task FKs resolve."""
    path = str(tmp_path / "heal.db")
    repo = SqlitePodcastRepository(db_path=path)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Heal Test Podcast",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Heal Test Episode",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return path


def _make_failed_task(
    qm: QueueManager,
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
    _backdate_completed(qm, task.id, completed_age_minutes)
    return task.id


def _backdate_completed(qm: QueueManager, task_id: str, age_minutes: float) -> None:
    stamp = (now_utc() - timedelta(minutes=age_minutes)).isoformat()
    con = sqlite3.connect(qm.db_path)
    con.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (stamp, task_id))
    con.commit()
    con.close()


class TestErrorClassPersisted:
    def test_schedule_retry_records_infra_class(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "infra")
        assert qm.get_task(task_id).error_class == "infra"

    def test_schedule_retry_records_item_class(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "item")
        assert qm.get_task(task_id).error_class == "item"

    def test_mark_dead_defaults_to_fatal_class(self, db_path):
        qm = QueueManager(db_path)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.DOWNSAMPLE)
        qm.mark_dead(task.id, "corrupt audio")
        assert qm.get_task(task.id).error_class == "fatal"


class TestCancellationNotHealable:
    def test_fail_task_clears_infra_class_so_healer_skips(self, db_path):
        # A user-cancelled retry_scheduled task that carried an 'infra' label
        # must NOT be resurrected by the healer (spec #49 review P2).
        qm = QueueManager(db_path)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        # Give it an infra label via a single scheduled retry (retry_scheduled).
        qm.schedule_retry(task.id, "Failed to connect: [Errno 8] nodename", error_class="infra")
        assert qm.get_task(task.id).error_class == "infra"

        # User cancels → fail_task. error_class must be cleared.
        qm.fail_task(task.id, "Pipeline cancelled by user")
        cancelled = qm.get_task(task.id)
        assert cancelled.status == TaskStatus.FAILED
        assert cancelled.error_class is None

        _backdate_completed(qm, task.id, 60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []


class TestFindHealableTasks:
    def test_selects_infra_failed_past_cooldown(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "infra", completed_age_minutes=60)
        healable = qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2)
        assert [t.id for t in healable] == [task_id]

    def test_excludes_item_class(self, db_path):
        qm = QueueManager(db_path)
        _make_failed_task(qm, "item", completed_age_minutes=60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_excludes_recent_failures_within_cooldown(self, db_path):
        qm = QueueManager(db_path)
        _make_failed_task(qm, "infra", completed_age_minutes=2)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_excludes_dead_tasks(self, db_path):
        qm = QueueManager(db_path)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        # A dead row carrying error_class='infra' must still never be healed.
        qm.mark_dead(task.id, "boom", error_class="infra")
        _backdate_completed(qm, task.id, 60)
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []

    def test_excludes_tasks_at_heal_cap(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "infra", completed_age_minutes=60)
        # Heal once (attempts -> 1), let it re-fail, heal again (-> 2 == cap).
        qm.heal_task(task_id, 2)
        for _ in range(qm.get_task(task_id).max_retries):
            qm.schedule_retry(task_id, "still down: [Errno 8] nodename nor servname", error_class="infra")
        _backdate_completed(qm, task_id, 60)
        qm.heal_task(task_id, 2)
        for _ in range(qm.get_task(task_id).max_retries):
            qm.schedule_retry(task_id, "still down: [Errno 8] nodename nor servname", error_class="infra")
        _backdate_completed(qm, task_id, 60)
        assert qm.get_task(task_id).heal_attempts == 2
        assert qm.find_healable_tasks(cooldown=timedelta(minutes=10), max_heal_attempts=2) == []


class TestHealTask:
    def test_requeues_and_resets_budget(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "infra")
        assert qm.get_task(task_id).retry_count == qm.get_task(task_id).max_retries

        healed = qm.heal_task(task_id, 2)
        assert healed is not None
        assert healed.status == TaskStatus.PENDING
        assert healed.retry_count == 0
        assert healed.heal_attempts == 1
        assert healed.last_heal_at is not None
        assert healed.last_error is None

    def test_refuses_dead(self, db_path):
        qm = QueueManager(db_path)
        task = qm.add_task(episode_id=EPISODE_ID, stage=TaskStage.TRANSCRIBE)
        qm.mark_dead(task.id, "boom", error_class="infra")
        assert qm.heal_task(task.id, 2) is None
        assert qm.get_task(task.id).status == TaskStatus.DEAD

    def test_refuses_over_cap(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "infra")
        # Cap of 0 → never heals.
        assert qm.heal_task(task_id, 0) is None
        assert qm.get_task(task_id).status == TaskStatus.FAILED

    def test_refuses_item_class(self, db_path):
        qm = QueueManager(db_path)
        task_id = _make_failed_task(qm, "item")
        assert qm.heal_task(task_id, 2) is None


class TestWorkerHealSweep:
    def _worker(self, qm: QueueManager) -> TaskWorker:
        return TaskWorker(
            qm,
            task_handlers={},
            auto_heal_enabled=True,
            heal_cooldown_minutes=10,
            max_heal_attempts=2,
        )

    def test_sweep_requeues_infra_failures(self, db_path):
        qm = QueueManager(db_path)
        infra_id = _make_failed_task(qm, "infra", completed_age_minutes=60)
        item_id = _make_failed_task(qm, "item", completed_age_minutes=60)

        healed = self._worker(qm)._heal_terminal_tasks()

        assert healed == 1
        assert qm.get_task(infra_id).status == TaskStatus.PENDING
        assert qm.get_task(item_id).status == TaskStatus.FAILED

    def test_sweep_noop_when_nothing_healable(self, db_path):
        qm = QueueManager(db_path)
        _make_failed_task(qm, "infra", completed_age_minutes=1)  # within cooldown
        assert self._worker(qm)._heal_terminal_tasks() == 0
