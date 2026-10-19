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

"""Spec #104 — a pipeline started on someone's behalf stops chaining once
nobody wants the episode (a ban cancelled the feed's queued work while a
stage was running); operator runs always chain."""

from __future__ import annotations

from datetime import datetime, timezone

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.core.task_worker import TaskWorker
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository


def _setup(tmp_path):
    db = str(tmp_path / "chain.db")
    SqlitePodcastRepository(db_path=db).save(
        Podcast(
            id="00000000-0000-0000-0000-000000000001",
            rss_url="https://example.com/feed.xml",
            title="Banned-only feed",
            description="",
            episodes=[
                Episode(
                    id="00000000-0000-0000-0000-0000000000e1",
                    external_id="e1",
                    title="e1",
                    description="",
                    pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    audio_url="https://example.com/e1.mp3",
                    duration=60,
                )
            ],
        )
    )
    queue = QueueManager(db)
    return queue, TaskWorker(queue_manager=queue, task_handlers={})


def _transcribes(queue):
    return [
        t
        for t in queue.get_tasks_for_episode("00000000-0000-0000-0000-0000000000e1")
        if t.stage == TaskStage.TRANSCRIBE
    ]


def test_running_stage_of_an_unwanted_episode_starts_nothing_further(tmp_path):
    queue, worker = _setup(tmp_path)
    task = queue.add_task(
        "00000000-0000-0000-0000-0000000000e1",
        TaskStage.DOWNSAMPLE,
        metadata={"run_full_pipeline": True, "initiated_by": "refresh"},
    )
    # Nobody follows the feed (the ban moved the only follow aside).
    worker._maybe_enqueue_next_stage(task)
    assert _transcribes(queue) == []


def test_operator_runs_still_chain(tmp_path):
    queue, worker = _setup(tmp_path)
    task = queue.add_task(
        "00000000-0000-0000-0000-0000000000e1", TaskStage.DOWNSAMPLE, metadata={"run_full_pipeline": True}
    )
    worker._maybe_enqueue_next_stage(task)
    [transcribe] = _transcribes(queue)
    assert transcribe.status == TaskStatus.PENDING
