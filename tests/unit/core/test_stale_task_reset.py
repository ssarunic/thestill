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

"""Regression: the stale-processing watchdog must actually fire.

``started_at`` is stored as ``now_utc().isoformat()`` — ISO-8601 with a 'T'
separator and a ``+00:00`` offset. ``reset_stale_tasks`` compared it with a
TEXT ``<`` against ``datetime('now', …)``, which renders a space-separated,
tz-naive string. Lexicographically ``'T' (84) > ' ' (32)`` at the separator, so
every stored value sorted AFTER the cutoff and the predicate matched zero rows
— tasks wedged in ``processing`` were never reclaimed, head-of-line-blocking
their stage (the symptom: a "stuck" clean queue). Fixed with ``julianday``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.utils.datetime_utils import now_utc

EPISODE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def qm(tmp_path: Path) -> QueueManager:
    db = str(tmp_path / "stale.db")
    repo = SqlitePodcastRepository(db_path=db)
    repo.save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Stale Test",
            description="",
            episodes=[
                Episode(
                    id=EPISODE_ID,
                    external_id="ep-1",
                    title="Stale Episode",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                ),
            ],
        )
    )
    return QueueManager(db)


def _processing_with_started_at(qm: QueueManager, stage: TaskStage, age_minutes: float) -> str:
    task = qm.add_task(episode_id=EPISODE_ID, stage=stage)
    # Write started_at in the real stored format: ISO-8601 with 'T' + offset.
    started = (now_utc() - timedelta(minutes=age_minutes)).isoformat()
    con = sqlite3.connect(qm.db_path)
    con.execute(
        "UPDATE tasks SET status='processing', started_at=? WHERE id=?",
        (started, task.id),
    )
    con.commit()
    con.close()
    return task.id


def test_resets_iso_format_stale_task(qm):
    task_id = _processing_with_started_at(qm, TaskStage.CLEAN, age_minutes=160)

    reset = qm.reset_stale_tasks(timeout_minutes=30)

    assert reset == 1
    assert qm.get_task(task_id).status == TaskStatus.PENDING


def test_leaves_fresh_processing_task(qm):
    task_id = _processing_with_started_at(qm, TaskStage.CLEAN, age_minutes=5)

    reset = qm.reset_stale_tasks(timeout_minutes=30)

    assert reset == 0
    assert qm.get_task(task_id).status == TaskStatus.PROCESSING


def test_unblocks_stage_capacity(qm):
    # The real-world symptom: wedged processing tasks saturate a stage so
    # pending work can't start. After reset, the freed row is dequeuable.
    _processing_with_started_at(qm, TaskStage.CLEAN, age_minutes=160)
    qm.reset_stale_tasks(timeout_minutes=30)

    nxt = qm.get_next_task(stage=TaskStage.CLEAN)
    assert nxt is not None  # the reclaimed task is now pending → claimable
