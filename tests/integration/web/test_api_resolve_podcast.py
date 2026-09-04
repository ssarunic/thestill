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

"""HTTP-level tests for ``POST /api/podcasts/resolve``.

The resolve endpoint is the lazy-import path used by the Top Podcasts list:
clicking an unimported chart entry hits this route to materialise the local
``podcasts`` row and get back its slug, then the UI navigates to the detail
page. The synchronous side runs ``podcast_service.add_podcast``; episode
discovery is handed off to a background daemon thread.

These tests exercise the route layer — auth, validation, idempotency, and
the slug-in-response contract. The actual feed-fetching mechanics are covered
by ``test_podcast_service.py`` and ``test_feed_manager.py`` upstream.
"""

from __future__ import annotations

from datetime import datetime, timezone

from thestill.models.podcast import Podcast


def test_resolve_existing_podcast_returns_slug_idempotently(client, app_state):
    """When the URL already maps to a ``podcasts`` row, the existing slug is
    returned and ``is_new`` is False. Whether a refresh is enqueued is the
    refresh-on-open service's call (spec #74) — this row has never been
    refreshed through the handler, so one is, and the response says so.
    """
    now = datetime.now(timezone.utc)
    podcast = Podcast(
        id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        rss_url="https://example.com/already.xml",
        title="Already Imported",
        description="",
        slug="already-imported",
        created_at=now,
        last_processed=now,
        episodes=[],
    )
    app_state.repository.save(podcast)

    response = client.post(
        "/api/podcasts/resolve",
        json={"url": "https://example.com/already.xml"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["podcast_slug"] == "already-imported"
    assert body["podcast_id"] == "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    assert body["is_new"] is False
    assert body["refresh_pending"] is True


def test_resolve_empty_url_rejected(client):
    response = client.post("/api/podcasts/resolve", json={"url": "   "})

    assert response.status_code == 400


def test_resolve_missing_url_rejected(client):
    response = client.post("/api/podcasts/resolve", json={})

    # Pydantic validation — missing required field
    assert response.status_code == 422


def test_resolve_invalid_url_returns_400(client, monkeypatch, app_state):
    """When ``add_podcast`` returns None (failed validation/fetch), the route
    surfaces it as a 400 rather than leaking the empty-result confusion."""

    def fake_add(url: str):
        return None

    monkeypatch.setattr(app_state.podcast_service, "add_podcast", fake_add)

    response = client.post(
        "/api/podcasts/resolve",
        json={"url": "https://bogus.invalid/feed.xml"},
    )

    assert response.status_code == 400


def test_resolve_new_podcast_returns_slug_and_enqueues_discovery(client, monkeypatch, app_state):
    """For genuinely new podcasts the slug is returned synchronously and the
    first discovery is a durable ``REFRESH_FEED`` task (spec #74) — observable
    via ``refresh_pending`` and retried by the worker, not a daemon thread.
    A second resolve coalesces onto the same task.
    """
    from thestill.core.queue_manager import TaskStage

    now = datetime.now(timezone.utc)
    new_podcast = Podcast(
        id="ffffffff-ffff-ffff-ffff-ffffffffffff",
        rss_url="https://example.com/fresh.xml",
        title="Fresh Feed",
        description="",
        slug="fresh-feed",
        created_at=now,
        episodes=[],
    )

    def fake_add(url: str):
        app_state.repository.save(new_podcast)
        return new_podcast

    monkeypatch.setattr(app_state.podcast_service, "add_podcast", fake_add)

    response = client.post("/api/podcasts/resolve", json={"url": "https://example.com/fresh.xml"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["podcast_slug"] == "fresh-feed"
    assert body["is_new"] is True
    assert body["refresh_pending"] is True
    task = app_state.queue_manager.get_next_task(stage=TaskStage.REFRESH_FEED)
    assert task is not None and task.podcast_id == new_podcast.id

    again = client.post("/api/podcasts/resolve", json={"url": "https://example.com/fresh.xml"})
    assert again.json()["refresh_pending"] is True
    assert app_state.queue_manager.get_next_task(stage=TaskStage.REFRESH_FEED) is None
