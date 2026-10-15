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

"""Spec #94 Phase B — per-page head tags, status codes, robots.txt and sitemap.xml."""

from __future__ import annotations

import re
import shutil
import urllib.robotparser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thestill.web.app import create_app
from thestill.web.middleware import rate_limit

from .test_public_api import _fresh_buckets, _seed, app_config  # noqa: F401 (fixtures)

FRONTEND_INDEX = Path(__file__).resolve().parents[3] / "thestill" / "web" / "frontend" / "index.html"


@pytest.fixture
def static_dir(tmp_path: Path, monkeypatch) -> Path:
    build = tmp_path / "static"
    (build / "assets").mkdir(parents=True)
    shutil.copy(FRONTEND_INDEX, build / "index.html")
    (build / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setenv("THESTILL_STATIC_DIR", str(build))
    return build


@pytest.fixture
def site(app_config, app_state, static_dir) -> TestClient:  # noqa: F811
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app)


def _head(html: str) -> str:
    return html.split("</head>", 1)[0]


def _title(html: str) -> str:
    return re.search(r"<title>(.*?)</title>", html, re.S).group(1)


def test_landing_has_indexable_tags(site, app_state):
    response = site.get("/")
    assert response.status_code == 200
    head = _head(response.text)
    assert "noindex" not in head
    assert '<link rel="canonical" href="https://thestill.test/" />' in head
    assert 'property="og:image" content="https://thestill.test/og-default.png"' in head
    assert "application/ld+json" in head
    assert response.headers["Cache-Control"] == "no-cache, must-revalidate"
    assert head.count("<title>") == 1


def test_head_request_is_allowed(site):
    assert site.head("/").status_code == 200


def test_public_show_and_episode_tags(site, app_state):
    _seed(app_state)
    show = site.get("/podcasts/public-show")
    assert show.status_code == 200
    assert _title(show.text).startswith("Public Show — episode summaries")
    episode = site.get("/podcasts/public-show/episodes/summarised")
    assert episode.status_code == 200
    head = _head(episode.text)
    assert "Episode 1 · Public Show" in _title(episode.text)
    assert 'content="Two hosts argue about chips."' in head
    assert '"@type":"PodcastEpisode"' in head
    assert "noindex" not in head


def test_hidden_unknown_and_unsummarised_pages_are_404_noindex(site, app_state):
    _seed(app_state)
    for path in (
        "/podcasts/hidden-show",
        "/podcasts/hidden-show/episodes/hidden-episode",
        "/podcasts/public-show/episodes/no-summary",
        "/podcasts/nope",
        "/podcasts/Bad_Slug",
    ):
        response = site.get(path)
        assert response.status_code == 404, path
        assert 'name="robots" content="noindex"' in _head(response.text), path


def test_private_app_paths_are_noindex(site):
    response = site.get("/inbox")
    assert response.status_code == 200
    assert 'name="robots" content="noindex"' in _head(response.text)


def test_metadata_cannot_break_out_of_the_head(site, app_state):
    _seed(app_state)
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute(
            "UPDATE episodes SET title = ? WHERE slug = 'summarised'",
            ('</title><script>alert(1)</script></script>"x',),
        )
    head = _head(site.get("/podcasts/public-show/episodes/summarised").text)
    assert "<script>alert(1)" not in head
    assert "&lt;/title&gt;&lt;script&gt;" in head
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.S).group(1)
    assert "</" not in ld and "\\u003c/script\\u003e" in ld


def test_root_files_are_served_as_files(site):
    response = site.get("/favicon.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"


def test_robots_allows_public_and_blocks_private(site):
    body = site.get("/robots.txt").text
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(body.splitlines())
    base = "https://thestill.test"
    for allowed in ("/podcasts/a/episodes/b", "/top", "/api/public/podcasts/a", "/api/auth/status"):
        assert parser.can_fetch("Googlebot", base + allowed), allowed
    for blocked in ("/inbox", "/api/inbox", "/api/podcasts/a/episodes/b/transcript", "/login"):
        assert not parser.can_fetch("Googlebot", base + blocked), blocked
    assert "Sitemap: https://thestill.test/sitemap.xml" in body


def test_sitemap_lists_exactly_the_public_pages(site, app_state):
    _seed(app_state)
    body = site.get("/sitemap.xml").text
    assert "https://thestill.test/podcasts/public-show/episodes/summarised" in body
    assert "https://thestill.test/podcasts/public-show</loc>" in body
    for absent in ("hidden-show", "no-summary", "not-applicable"):
        assert absent not in body
    assert site.get("/sitemap-9.xml").status_code == 404


def test_shell_lookups_are_rate_limited_but_cached_hits_are_free(site, app_state, monkeypatch):
    _seed(app_state)
    monkeypatch.setattr(rate_limit, "PUBLIC_LIMIT", rate_limit.RateLimit(max_events=2, window_seconds=60))
    assert [site.get("/podcasts/public-show").status_code for _ in range(5)] == [200] * 5
    assert site.get("/podcasts/unknown-a").status_code == 404
    assert site.get("/podcasts/unknown-b").status_code == 429


def test_single_user_installs_are_not_indexed(tmp_path, static_dir):
    from types import SimpleNamespace

    from thestill.utils.config import Config

    config = Config(storage_path=tmp_path, multi_user=False, jwt_secret_key="test" * 16, cookie_secure=False)
    app = create_app(config)
    app.state.app_state = SimpleNamespace(config=config)
    client = TestClient(app)
    assert client.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
    assert client.get("/sitemap.xml").status_code == 404
    # The shell is served untouched: the build's default head, no lookups.
    assert _title(client.get("/podcasts/anything").text) == "Thestill"
