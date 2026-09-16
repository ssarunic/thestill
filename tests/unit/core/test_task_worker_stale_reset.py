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

"""The worker's stale sweep never requeues its own live handlers (2026-09-16)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.config import get_stale_timeout_seconds_per_stage
from thestill.utils.datetime_utils import now_utc

EPISODES = [f"00000000-0000-0000-0000-0000000000{n:02d}" for n in (1, 2)]


@pytest.fixture
def qm(tmp_path: Path) -> QueueManager:
    db = str(tmp_path / "worker-stale.db")
    repo = SqlitePodcastRepository(db_path=db)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Stale",
            description="",
            episodes=[
                Episode(
                    id=eid,
                    external_id=f"ep-{i}",
                    title=f"Episode {i}",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url=f"https://example.com/{i}.mp3",
                    duration=60,
                )
                for i, eid in enumerate(EPISODES)
            ],
        )
    )
    return QueueManager(db)


def _old_processing(qm: QueueManager, episode_id: str, stage: TaskStage, age_minutes: float):
    task = qm.add_task(episode_id=episode_id, stage=stage)
    claimed = qm.get_next_task(stage=stage)
    assert claimed is not None and claimed.id == task.id
    started = (now_utc() - timedelta(minutes=age_minutes)).isoformat()
    con = sqlite3.connect(qm.db_path)
    con.execute("UPDATE tasks SET started_at=? WHERE id=?", (started, task.id))
    con.commit()
    con.close()
    return qm.get_task(task.id)


def test_sweep_skips_the_handler_this_worker_is_running(qm):
    live = _old_processing(qm, EPISODES[0], TaskStage.COMPUTE_RELATED, age_minutes=200)
    dead = _old_processing(qm, EPISODES[1], TaskStage.COMPUTE_RELATED, age_minutes=200)
    worker = TaskWorker(qm, {}, stale_timeout_minutes=30)
    worker._active_by_stage[TaskStage.COMPUTE_RELATED][live.episode_id] = live

    worker._reset_stale_tasks()

    assert qm.get_task(live.id).status == TaskStatus.PROCESSING
    assert qm.get_task(dead.id).status == TaskStatus.PENDING


def test_sweep_uses_per_stage_windows_when_given(qm):
    slow = _old_processing(qm, EPISODES[0], TaskStage.COMPUTE_RELATED, age_minutes=100)
    quick = _old_processing(qm, EPISODES[1], TaskStage.CLEAN, age_minutes=100)
    worker = TaskWorker(
        qm,
        {},
        stale_timeout_minutes=30,
        stale_timeout_per_stage={TaskStage.COMPUTE_RELATED: 7800.0, TaskStage.CLEAN: 1800.0},
    )

    worker._reset_stale_tasks()

    assert qm.get_task(slow.id).status == TaskStatus.PROCESSING  # inside its two-hour-plus window
    assert qm.get_task(quick.id).status == TaskStatus.PENDING


def test_per_stage_windows_cover_the_watchdog(monkeypatch):
    for key in ("QUEUE_STALE_TIMEOUT_SECONDS", "QUEUE_STALE_TIMEOUT_MARGIN_SECONDS", "QUEUE_STAGE_WATCHDOG_SECONDS"):
        monkeypatch.delenv(key, raising=False)
    windows = get_stale_timeout_seconds_per_stage()
    assert windows[TaskStage.COMPUTE_RELATED] == 7200.0 + 600.0
    assert windows[TaskStage.CLEAN] == 5400.0 + 600.0
    assert windows[TaskStage.TRANSCRIBE] == 1800.0  # unbounded watchdog → global floor
    monkeypatch.setenv("QUEUE_STALE_TIMEOUT_SECONDS", "9000")
    assert get_stale_timeout_seconds_per_stage()[TaskStage.COMPUTE_RELATED] == 9000.0  # floor wins
