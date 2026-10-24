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

"""Spec #95 ``follow`` / ``unfollow`` usage events over HTTP: one event per
follow actually made, labelled with where in the app it was made."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thestill.models.podcast import Podcast
from thestill.models.user import User
from thestill.services.analytics_sink import Follow, Unfollow
from thestill.utils.config import Config
from thestill.web.app import create_app
from thestill.web.dependencies import AUTH_COOKIE_NAME
from thestill.web.routes.api_commands import run_add_podcast_task


class RecordingSink:
    enabled = True
    browser_key = "phc_test"

    def __init__(self):
        self.events = []

    def capture(self, event, *, user=None, visitor_key=None, gpc=False):
        self.events.append((event, user.id if user else None, gpc))

    def shutdown(self):
        pass


@pytest.fixture
def app_config(tmp_path: Path) -> Config:
    return Config(
        storage_path=tmp_path,
        multi_user=True,
        google_client_id="test-client",
        google_client_secret="test-secret",
        jwt_secret_key="test" * 16,
        cookie_secure=False,
    )


@pytest.fixture
def sink(app_state) -> RecordingSink:
    app_state.analytics_sink = RecordingSink()
    return app_state.analytics_sink


@pytest.fixture
def user(app_state) -> User:
    app_state.user_repository.save(User(email="reader@example.com"))
    return app_state.user_repository.get_by_email("reader@example.com")


@pytest.fixture
def client(app_config, app_state, user) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    client = TestClient(app)
    client.cookies.set(AUTH_COOKIE_NAME, app_state.auth_service.create_jwt(user))
    return client


def _podcast(app_state, slug: str) -> Podcast:
    podcast = Podcast(rss_url=f"https://example.com/{slug}.xml", title=slug, description="", slug=slug)
    app_state.repository.save(podcast)
    return app_state.repository.get_by_url(f"https://example.com/{slug}.xml")


def test_follow_sends_one_event_with_its_source(client, app_state, sink, user):
    _podcast(app_state, "show")
    assert client.post("/api/podcasts/show/follow?source=episode_prompt").status_code == 201
    assert sink.events == [(Follow(source="episode_prompt"), user.id, False)]

    # Already following: a 409 and no second event.
    assert client.post("/api/podcasts/show/follow?source=episode_prompt").status_code == 409
    assert len(sink.events) == 1


def test_an_unknown_or_missing_source_is_other(client, app_state, sink):
    _podcast(app_state, "a")
    _podcast(app_state, "b")
    assert client.post("/api/podcasts/a/follow?source=<script>").status_code == 201
    assert client.post("/api/podcasts/b/follow").status_code == 201
    assert [event for event, _, _ in sink.events] == [Follow(source="other"), Follow(source="other")]


def test_unfollow_sends_an_event(client, app_state, sink, user):
    _podcast(app_state, "show")
    client.post("/api/podcasts/show/follow?source=show_page")
    assert client.delete("/api/podcasts/show/follow").status_code == 204
    assert sink.events[-1] == (Unfollow(), user.id, False)


def test_gpc_follow_sends_nothing_identifying(client, app_state, sink):
    _podcast(app_state, "show")
    client.post("/api/podcasts/show/follow?source=show_page", headers={"Sec-GPC": "1"})
    assert [gpc for _, _, gpc in sink.events] == [True]  # the sink drops it (test_analytics_sink)


def test_add_podcast_reports_only_a_new_follow(app_state, user, monkeypatch):
    podcast = _podcast(app_state, "charted")
    monkeypatch.setattr(app_state.podcast_service, "add_podcast", lambda url, precheck=None: podcast)
    followed = []

    run_add_podcast_task(app_state, podcast.rss_url, user.id, on_followed=lambda: followed.append(1))
    assert followed == [1]
    assert app_state.follower_repository.exists(user.id, podcast.id)

    # Adding a show already followed is not a follow.
    run_add_podcast_task(app_state, podcast.rss_url, user.id, on_followed=lambda: followed.append(2))
    assert followed == [1]
