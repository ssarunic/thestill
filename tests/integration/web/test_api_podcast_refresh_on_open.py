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

"""Spec #74 — ``GET /api/podcasts/{slug}`` enqueues one throttled
``REFRESH_FEED`` and reports ``refresh_pending`` until it lands."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from thestill.core.queue_manager import TaskStage
from thestill.models.podcast import Podcast


def _podcast(slug: str, *, discovered: bool) -> Podcast:
    now = datetime.now(timezone.utc)
    return Podcast(
        id=f"aaaaaaaa-aaaa-aaaa-aaaa-{abs(hash(slug)) % 10**12:012d}",
        rss_url=f"https://example.com/{slug}.xml",
        title=slug,
        description="",
        slug=slug,
        created_at=now,
        last_processed=(now - timedelta(days=30)) if discovered else None,
        episodes=[],
    )


def test_open_enqueues_refresh_and_reports_pending(client, app_state):
    podcast = _podcast("opened-show", discovered=True)
    app_state.repository.save(podcast)

    first = client.get("/api/podcasts/opened-show")
    assert first.status_code == 200, first.text
    assert first.json()["podcast"]["refresh_pending"] is True
    assert app_state.queue_manager.has_pending_feed_task(podcast.id, TaskStage.REFRESH_FEED)

    # A reload while the task is queued coalesces — still pending, still one task.
    second = client.get("/api/podcasts/opened-show")
    assert second.json()["podcast"]["refresh_pending"] is True
    task = app_state.queue_manager.get_next_task(stage=TaskStage.REFRESH_FEED)
    assert task is not None and task.podcast_id == podcast.id
    assert app_state.queue_manager.get_next_task(stage=TaskStage.REFRESH_FEED) is None


def test_open_of_half_imported_podcast_enqueues_discovery(client, app_state):
    """A row whose first discovery never completed (``last_processed`` NULL)
    is not locked out: the open enqueues the discovery and reports pending,
    so the page keeps polling until episodes land."""
    podcast = _podcast("half-imported", discovered=False)
    app_state.repository.save(podcast)

    response = client.get("/api/podcasts/half-imported")
    assert response.status_code == 200, response.text
    assert response.json()["podcast"]["refresh_pending"] is True
    assert app_state.queue_manager.has_pending_feed_task(podcast.id, TaskStage.REFRESH_FEED)
