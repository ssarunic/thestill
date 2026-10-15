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

"""Spec #94 Phase A — the signed-out public API under ``/api/public``.

The app runs in multi-user mode (the only mode that mounts it) against a
tmp SQLite corpus: one public show with a summarised episode, an episode
with no summary, an ``N/A`` summary, and a hidden show.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from thestill.models.podcast import Episode, Podcast
from thestill.utils.config import Config
from thestill.web import dependencies
from thestill.web.app import create_app
from thestill.web.middleware import rate_limit
from thestill.web.routes import api_public

REAL_SUMMARY = "## 1. 🎙️ The Gist\n\nTwo hosts argue about chips.\n\n## 2. Timeline\n\n- [01:00] Start\n"
FORBIDDEN_KEYS = {
    "rss_url",
    "state",
    "external_id",
    "refresh_pending",
    "is_following",
    "failure_reason",
    "failed_at_stage",
    "origin",
    "import_kind",
    "playback",
    "user_region",
}


@pytest.fixture
def app_config(tmp_path: Path) -> Config:
    return Config(
        storage_path=tmp_path,
        multi_user=True,
        public_base_url="https://thestill.test",
        google_client_id="test-client",
        google_client_secret="test-secret",
        jwt_secret_key="test" * 16,
        cookie_secure=False,
    )


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    rate_limit.reset_for_testing()
    api_public.reset_recent_cache_for_testing()
    yield
    rate_limit.reset_for_testing()


@pytest.fixture
def anon(app_config: Config, app_state) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app)


def _write_summary(app_state, name: str, content: str) -> str:
    path = app_state.path_manager.summary_file(name)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")
    return name


def _seed(app_state) -> None:
    now = datetime.now(timezone.utc)

    def episode(n: int, slug: str, summary: str | None, preview: str | None) -> Episode:
        return Episode(
            id=f"00000000-0000-0000-0000-00000000000{n}",
            external_id=f"guid-{n}",
            title=f"Episode {n}",
            description="Publisher notes.",
            slug=slug,
            pub_date=now - timedelta(days=n),
            audio_url=f"https://example.com/{n}.mp3",
            summary_path=_write_summary(app_state, f"{slug}_summary.md", summary) if summary else None,
            summary_preview=preview,
            raw_transcript_path=f"{slug}_transcript.json",
            clean_transcript_path=f"{slug}_clean.md",
        )

    public = Podcast(
        id="11111111-1111-1111-1111-111111111111",
        rss_url="https://example.com/public.xml",
        title="Public Show",
        description="A show about chips.",
        slug="public-show",
        created_at=now,
        episodes=[
            episode(1, "summarised", REAL_SUMMARY, "Two hosts argue about chips."),
            episode(2, "no-summary", None, None),
            episode(3, "not-applicable", "N/A — this episode is a trailer.", ""),
        ],
    )
    hidden = Podcast(
        id="22222222-2222-2222-2222-222222222222",
        rss_url="https://example.com/hidden.xml",
        title="Hidden Show",
        description="",
        slug="hidden-show",
        created_at=now,
        episodes=[episode(4, "hidden-episode", REAL_SUMMARY, "Two hosts argue about chips.")],
    )
    for podcast in (public, hidden):
        app_state.repository.save(podcast)
        for ep in podcast.episodes:
            if ep.summary_preview is not None:
                app_state.repository.set_episode_summary_preview(ep.id, ep.summary_preview)
    app_state.repository.set_podcast_public_pages(hidden.id, False)


def _walk_keys(value) -> set:
    keys: set = set()
    if isinstance(value, dict):
        for k, v in value.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            keys |= _walk_keys(v)
    return keys


def _assert_clean(body: dict) -> None:
    keys = _walk_keys(body)
    assert not keys & FORBIDDEN_KEYS, keys & FORBIDDEN_KEYS
    assert not [k for k in keys if k.endswith("_path")]


def test_public_podcast_is_readable_without_a_session(anon, app_state):
    _seed(app_state)
    response = anon.get("/api/public/podcasts/public-show")
    assert response.status_code == 200, response.text
    podcast = response.json()["podcast"]
    assert podcast["title"] == "Public Show"
    assert podcast["summarised_count"] == 1
    _assert_clean(response.json())
    assert response.headers["Cache-Control"].startswith("public")
    assert response.headers["Vary"] == "Cookie, Authorization"


def test_hidden_and_unknown_podcasts_are_404(anon, app_state):
    _seed(app_state)
    assert anon.get("/api/public/podcasts/hidden-show").status_code == 404
    assert anon.get("/api/public/podcasts/hidden-show/episodes/hidden-episode").status_code == 404
    assert anon.get("/api/public/podcasts/nope").status_code == 404
    assert anon.get("/api/public/podcasts/Not_A_Slug").status_code == 404


def test_episode_list_has_only_real_summaries(anon, app_state):
    _seed(app_state)
    body = anon.get("/api/public/podcasts/public-show/episodes").json()
    assert [e["slug"] for e in body["episodes"]] == ["summarised"]
    assert body["total"] == 1
    _assert_clean(body)


def test_episode_without_a_real_summary_is_404(anon, app_state):
    _seed(app_state)
    assert anon.get("/api/public/podcasts/public-show/episodes/no-summary").status_code == 404
    assert anon.get("/api/public/podcasts/public-show/episodes/not-applicable").status_code == 404
    assert anon.get("/api/public/podcasts/public-show/episodes/not-applicable/summary").status_code == 404


def test_public_episode_shape(anon, app_state):
    _seed(app_state)
    response = anon.get("/api/public/podcasts/public-show/episodes/summarised")
    assert response.status_code == 200, response.text
    episode = response.json()["episode"]
    assert episode["has_transcript"] is True
    assert episode["podcast"]["slug"] == "public-show"
    _assert_clean(response.json())


def test_summary_never_calls_an_llm_or_translates(anon, app_state, monkeypatch):
    _seed(app_state)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("the public summary route must not build an LLM provider")

    monkeypatch.setattr("thestill.core.llm_provider.create_llm_provider_from_config", _boom)
    response = anon.get("/api/public/podcasts/public-show/episodes/summarised/summary")
    assert response.status_code == 200, response.text
    assert "Two hosts argue" in response.json()["content"]
    assert "citations" not in response.json()
    assert anon.get("/api/public/podcasts/public-show/episodes/summarised/summary?lang=fr").status_code == 404


def test_opening_a_show_never_enqueues_a_refresh(anon, app_state, monkeypatch):
    _seed(app_state)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("public reads must not trigger refresh-on-open")

    monkeypatch.setattr(app_state.refresh_on_open, "maybe_trigger", _boom)
    assert anon.get("/api/public/podcasts/public-show").status_code == 200


def test_recent_summaries_feed(anon, app_state):
    _seed(app_state)
    body = anon.get("/api/public/recent-summaries?limit=6").json()
    assert [e["slug"] for e in body["episodes"]] == ["summarised"]
    assert body["episodes"][0]["podcast"]["slug"] == "public-show"
    assert anon.get("/api/public/recent-summaries?limit=50").status_code == 422


def test_credentialed_requests_are_private(anon, app_state):
    _seed(app_state)
    for headers in ({"Authorization": "Bearer not-a-real-token"}, {"Cookie": f"{dependencies.AUTH_COOKIE_NAME}=x"}):
        response = anon.get("/api/public/podcasts/public-show", headers=headers)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-cache"
        assert response.headers["Vary"] == "Cookie, Authorization"


def test_anonymous_callers_are_rate_limited(anon, app_state, monkeypatch):
    _seed(app_state)
    monkeypatch.setattr(rate_limit, "PUBLIC_LIMIT", rate_limit.RateLimit(max_events=3, window_seconds=60))
    codes = [anon.get("/api/public/podcasts/public-show").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_only_public_and_auth_routes_skip_authentication(app_config, app_state):
    """Default-deny holds: every /api route needs a session unless it is on
    the written allowlist (spec #94 FM: a new router silently public)."""
    app = create_app(app_config)
    guards = {dependencies.require_auth, dependencies.require_admin}

    def guarded(route: APIRoute) -> bool:
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            if dep.call in guards:
                return True
            stack.extend(dep.dependencies)
        return False

    open_routes = sorted(
        route.path
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/") and not guarded(route)
    )
    assert open_routes
    assert all(path.startswith(("/api/public/", "/api/auth/")) for path in open_routes), open_routes


def test_single_user_mode_mounts_no_public_api(tmp_path):
    config = Config(storage_path=tmp_path, multi_user=False, jwt_secret_key="test" * 16, cookie_secure=False)
    app = create_app(config)
    assert not [r for r in app.routes if getattr(r, "path", "").startswith("/api/public")]
