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

"""Spec #105 Phase 4 — IndexNow key file and pings."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from thestill.services import indexnow as indexnow_module
from thestill.services.indexnow import ENDPOINT, IndexNow
from thestill.web.app import create_app

from .test_public_api import _fresh_buckets, _seed, app_config  # noqa: F401
from .test_public_shell import static_dir  # noqa: F401

KEY = "test-indexnow-key"


class _Post:
    def __init__(self, status=202, error=None):
        self.calls = []
        self.status = status
        self.error = error

    def __call__(self, url, json, timeout):
        self.calls.append((url, json))
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status)


def _config(**overrides):
    base = dict(indexnow_key=KEY, public_base_url="https://thestill.test", multi_user=True, public_pages_enabled=True)
    return SimpleNamespace(**{**base, **overrides})


def test_submit_posts_absolute_urls_with_the_key_location():
    post = _Post()
    assert IndexNow(_config(), post=post).submit(["/podcasts/a", "/podcasts/a", "/podcasts/a/episodes/b"]) == 202
    ((url, body),) = post.calls
    assert url == ENDPOINT
    assert body == {
        "host": "thestill.test",
        "key": KEY,
        "keyLocation": f"https://thestill.test/{KEY}.txt",
        "urlList": ["https://thestill.test/podcasts/a", "https://thestill.test/podcasts/a/episodes/b"],
    }


@pytest.mark.parametrize(
    "overrides", [{"indexnow_key": ""}, {"multi_user": False}, {"public_pages_enabled": False}, {"public_base_url": ""}]
)
def test_off_unless_public_pages_and_a_key(overrides):
    post = _Post()
    indexnow = IndexNow(_config(**overrides), post=post)
    assert not indexnow.enabled and indexnow.submit(["/podcasts/a"]) is None and post.calls == []


def test_a_failed_ping_is_logged_not_raised():
    assert IndexNow(_config(), post=_Post(error=OSError("down"))).submit(["/podcasts/a"]) is None


def test_key_file_is_served_only_when_configured(app_config, app_state, static_dir):  # noqa: F811
    app_config.indexnow_key = KEY
    app = create_app(app_config)
    app.state.app_state = app_state
    response = TestClient(app).get(f"/{KEY}.txt")
    assert response.status_code == 200 and response.text == KEY

    app_config.indexnow_key = ""
    app = create_app(app_config)
    app.state.app_state = app_state
    assert TestClient(app).get(f"/{KEY}.txt").text != KEY


def test_a_newly_published_episode_is_pinged_with_its_show(app_config, app_state, monkeypatch):  # noqa: F811
    from thestill.core import task_handlers

    _seed(app_state)
    app_config.indexnow_key = KEY
    sent = []
    monkeypatch.setattr(indexnow_module.IndexNow, "submit_in_background", lambda self, paths: sent.append(list(paths)))
    podcast, _ = app_state.repository.get_episode("00000000-0000-0000-0000-000000000001")
    task_handlers._ping_indexnow_on_publish(app_state, podcast, "00000000-0000-0000-0000-000000000001")
    assert sent == [["/podcasts/public-show/episodes/summarised", "/podcasts/public-show"]]
    # No public page (an N/A summary stores an empty preview): no ping.
    task_handlers._ping_indexnow_on_publish(app_state, podcast, "00000000-0000-0000-0000-000000000003")
    assert len(sent) == 1


def test_visibility_change_lists_the_show_and_its_public_episodes(app_state):
    from thestill.cli import _public_page_paths

    _seed(app_state)
    podcast, _ = app_state.repository.get_episode("00000000-0000-0000-0000-000000000001")
    assert _public_page_paths(app_state.repository, podcast) == [
        "/podcasts/public-show",
        "/podcasts/public-show/episodes/summarised",
    ]
