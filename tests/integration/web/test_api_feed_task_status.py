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

"""Regression: ``GET /api/commands/task/{id}`` must not 500 for feed tasks.

Spec #48 REFRESH_FEED tasks are podcast-scoped (``episode_id=None``). The
response model required ``episode_id: str``, so serializing a feed task raised
a validation error → 500. ``episode_id`` is now nullable and ``podcast_id`` is
returned alongside it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.podcast import Podcast
from thestill.web.dependencies import AppState

PODCAST_ID = "00000000-0000-0000-0000-0000000000fe"


def _seed_podcast(app_state: AppState) -> None:
    app_state.repository.save(
        Podcast(
            id=PODCAST_ID,
            rss_url="https://example.com/feed-task.xml",
            title="Feed Task Podcast",
            description="",
            episodes=[],
        )
    )


def test_feed_task_status_returns_200_with_podcast_id(client: TestClient, app_state: AppState) -> None:
    _seed_podcast(app_state)
    qm: QueueManager = app_state.queue_manager
    task = qm.add_feed_task(PODCAST_ID)

    resp = client.get(f"/api/commands/task/{task.id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == task.id
    assert body["stage"] == TaskStage.REFRESH_FEED.value
    assert body["episode_id"] is None
    assert body["podcast_id"] == PODCAST_ID


def test_episode_task_status_still_includes_episode_id(client: TestClient, app_state: AppState) -> None:
    # An episode-scoped task keeps episode_id populated and podcast_id null.
    _seed_podcast(app_state)
    qm: QueueManager = app_state.queue_manager
    # Episode FK: reuse the podcast's episode space via a direct task add.
    from thestill.models.podcast import Episode

    app_state.repository.save(
        Podcast(
            id="00000000-0000-0000-0000-0000000000ff",
            rss_url="https://example.com/ep-feed.xml",
            title="Ep Podcast",
            description="",
            episodes=[
                Episode(
                    id="22222222-2222-2222-2222-2222222222fe",
                    external_id="ep-1",
                    title="Ep",
                    description="",
                    audio_url="https://example.com/ep1.mp3",
                    duration=60,
                )
            ],
        )
    )
    task = qm.add_task(episode_id="22222222-2222-2222-2222-2222222222fe", stage=TaskStage.DOWNLOAD)

    resp = client.get(f"/api/commands/task/{task.id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["episode_id"] == "22222222-2222-2222-2222-2222222222fe"
    assert body["podcast_id"] is None
