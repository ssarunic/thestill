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

"""Spec #31 — ``POST /api/imports`` integration tests."""

import sqlite3

import pytest

from thestill.core.queue_manager import TaskStage
from thestill.repositories.sqlite_podcast_repository import SYNTHETIC_AUDIO_IMPORTS_ID
from thestill.services.import_service import ImportService, YouTubeResolver


def test_post_imports_returns_201_shape_and_creates_row(client, app_state):
    response = client.post("/api/imports", json={"url": "https://example.com/foo.mp3"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    payload = body["import"]
    assert payload["episode_id"]
    assert payload["canonical_id"].startswith("audio:")
    assert payload["kind"] == "bare_audio"
    assert payload["deduplicated"] is False
    assert payload["inbox_created"] is True
    assert payload["inbox_entry"]["source"] == "import"
    # Bare-audio imports park under the synthetic parent — no follow target.
    assert payload["parent"] is None

    # Synthetic parent + episode + inbox row are persisted.
    db_path = app_state.repository.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        parent = conn.execute(
            "SELECT synthetic, auto_added FROM podcasts WHERE id = ?",
            (SYNTHETIC_AUDIO_IMPORTS_ID,),
        ).fetchone()
        assert dict(parent) == {"synthetic": 1, "auto_added": 0}

        ep = conn.execute(
            "SELECT podcast_id, canonical_id FROM episodes WHERE id = ?",
            (payload["episode_id"],),
        ).fetchone()
        assert ep["podcast_id"] == SYNTHETIC_AUDIO_IMPORTS_ID
        assert ep["canonical_id"] == payload["canonical_id"]

    # The TRANSCRIBE task was queued — imports skip download/downsample and let
    # the Dalston transcribe handler fetch the audio from the URL.
    task = app_state.queue_manager.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert task is not None
    assert task.episode_id == payload["episode_id"]
    assert app_state.queue_manager.get_next_task(stage=TaskStage.DOWNLOAD) is None


def test_post_imports_idempotent_for_same_url(client, app_state):
    r1 = client.post("/api/imports", json={"url": "https://example.com/ep.mp3"})
    r2 = client.post(
        "/api/imports",
        json={"url": "https://EXAMPLE.com/ep.mp3?utm_source=x"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    e1 = r1.json()["import"]
    e2 = r2.json()["import"]

    assert e1["episode_id"] == e2["episode_id"]
    assert e2["deduplicated"] is True
    assert e2["inbox_created"] is False

    # Pipeline only runs once.
    first = app_state.queue_manager.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert first is not None
    assert first.episode_id == e1["episode_id"]
    assert app_state.queue_manager.get_next_task(stage=TaskStage.TRANSCRIBE) is None


def test_post_imports_unsupported_url_returns_400(client):
    # Vimeo isn't covered by any v1 resolver.
    response = client.post("/api/imports", json={"url": "https://vimeo.com/123456789"})
    assert response.status_code == 400
    assert "No resolver" in response.json()["detail"]


def test_post_imports_empty_url_returns_400(client):
    response = client.post("/api/imports", json={"url": "   "})
    assert response.status_code == 400


@pytest.fixture
def fake_youtube_video_info():
    return {
        "id": "dQw4w9WgXcQ",
        "title": "Never Gonna Give You Up",
        "description": "Music video",
        "channel": "Rick Astley",
        "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
        "uploader": "Rick Astley",
        "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "duration": 213,
        "upload_date": "20091025",
        "thumbnails": [{"url": "https://i.ytimg.com/hi.jpg"}],
    }


def test_post_imports_youtube_returns_parent(client, app_state, fake_youtube_video_info):
    """A YouTube import surfaces the auto-added channel as the parent so the
    UI can render a 'Follow this channel' CTA without a second round-trip."""
    # Swap the resolver lineup so we don't actually invoke yt-dlp.
    app_state.import_service = ImportService(
        repository=app_state.repository,
        inbox_repository=app_state.inbox_repository,
        queue_manager=app_state.queue_manager,
        resolvers=[YouTubeResolver(metadata_fetcher=lambda url: fake_youtube_video_info)],
    )

    response = client.post("/api/imports", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    assert response.status_code == 200, response.text
    payload = response.json()["import"]
    assert payload["kind"] == "youtube"
    parent = payload["parent"]
    assert parent is not None
    assert parent["title"] == "Rick Astley"
    assert parent["slug"] == "rick-astley"
    assert parent["id"]


def test_post_imports_youtube_dedup_still_returns_parent(client, app_state, fake_youtube_video_info):
    """Dedup hit also returns the parent so re-imports drive the same CTA."""
    app_state.import_service = ImportService(
        repository=app_state.repository,
        inbox_repository=app_state.inbox_repository,
        queue_manager=app_state.queue_manager,
        resolvers=[YouTubeResolver(metadata_fetcher=lambda url: fake_youtube_video_info)],
    )

    r1 = client.post("/api/imports", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
    r2 = client.post("/api/imports", json={"url": "https://youtu.be/dQw4w9WgXcQ"})

    assert r1.status_code == 200
    assert r2.status_code == 200
    p1 = r1.json()["import"]["parent"]
    p2 = r2.json()["import"]["parent"]
    assert p1 == p2
    assert r2.json()["import"]["deduplicated"] is True
