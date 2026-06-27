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

"""``QueueManager.sum_duration_by_stage`` — per-stage queued audio length.

Backs the queue viewer's "time to process" estimate. The contract that
matters: the total is summed over the full DB (not the capped display
list), counts only the requested statuses, joins through to ``episodes``
so feed-scoped tasks are excluded, and tolerates episodes with no
``duration``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

PODCAST_ID = "00000000-0000-0000-0000-000000000001"


def _episode(n: int, duration: int | None) -> Episode:
    return Episode(
        id=f"{n:08d}-0000-0000-0000-000000000000",
        external_id=f"ep-{n}",
        title=f"Episode {n}",
        description="",
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        audio_url=f"https://example.com/ep{n}.mp3",
        duration=duration,
    )


@pytest.fixture
def qm(tmp_path: Path) -> QueueManager:
    """Queue + repo over one podcast with a mix of episode durations."""
    path = str(tmp_path / "durations.db")
    repo = SqlitePodcastRepository(db_path=path)
    repo.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed.xml",
            title="Duration Test Podcast",
            description="",
            episodes=[
                _episode(1, 600),  # 10m
                _episode(2, 1800),  # 30m
                _episode(3, 3600),  # 1h
                _episode(4, None),  # no itunes:duration
            ],
        )
    )
    return QueueManager(db_path=path)


def _eid(n: int) -> str:
    return f"{n:08d}-0000-0000-0000-000000000000"


def test_sums_pending_and_processing_by_stage(qm: QueueManager) -> None:
    qm.add_task(_eid(1), TaskStage.TRANSCRIBE)  # 600 pending
    qm.add_task(_eid(2), TaskStage.TRANSCRIBE)  # 1800 pending
    qm.add_task(_eid(3), TaskStage.DOWNLOAD)  # 3600 different stage

    # Claim one transcribe task so it is 'processing', not 'pending'.
    qm.get_next_task(stage=TaskStage.TRANSCRIBE)

    totals = qm.sum_duration_by_stage()

    assert totals[TaskStage.TRANSCRIBE.value] == 2400  # pending + processing
    assert totals[TaskStage.DOWNLOAD.value] == 3600


def test_null_duration_episode_contributes_nothing(qm: QueueManager) -> None:
    qm.add_task(_eid(1), TaskStage.TRANSCRIBE)  # 600
    qm.add_task(_eid(4), TaskStage.TRANSCRIBE)  # duration is None

    totals = qm.sum_duration_by_stage()

    assert totals[TaskStage.TRANSCRIBE.value] == 600


def test_status_filter_excludes_completed(qm: QueueManager) -> None:
    qm.add_task(_eid(1), TaskStage.TRANSCRIBE)
    task = qm.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert task is not None
    qm.complete_task(task.id)

    # Default statuses (pending + processing) no longer see the completed task.
    assert qm.sum_duration_by_stage() == {}
    # Explicitly asking for completed surfaces it.
    assert qm.sum_duration_by_stage(["completed"])[TaskStage.TRANSCRIBE.value] == 600


def test_feed_scoped_tasks_are_excluded(qm: QueueManager) -> None:
    # Feed (REFRESH_FEED) tasks carry podcast_id and no episode, so the inner
    # join drops them — they have no audio length to estimate.
    qm.add_feed_task(PODCAST_ID)
    qm.add_task(_eid(1), TaskStage.TRANSCRIBE)

    totals = qm.sum_duration_by_stage()

    assert TaskStage.REFRESH_FEED.value not in totals
    assert totals[TaskStage.TRANSCRIBE.value] == 600


def test_empty_queue_returns_empty_mapping(qm: QueueManager) -> None:
    assert qm.sum_duration_by_stage() == {}
