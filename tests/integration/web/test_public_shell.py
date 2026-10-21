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
import structlog
from fastapi.testclient import TestClient

from thestill.web import public_shell
from thestill.web.app import create_app
from thestill.web.dependencies import AUTH_COOKIE_NAME
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


def test_robots_blocks_training_crawlers_but_not_search_or_user_fetches(site):
    """Spec #105 decision 1."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(site.get("/robots.txt").text.splitlines())
    page = "https://thestill.test/podcasts/a/episodes/b"
    for agent in public_shell.TRAINING_CRAWLERS:
        assert not parser.can_fetch(agent, page), agent
        assert not parser.can_fetch(agent, "https://thestill.test/"), agent
    for agent in (
        "Bingbot",
        "OAI-SearchBot",
        "ChatGPT-User",
        "Claude-SearchBot",
        "Claude-User",
        "PerplexityBot",
        "Perplexity-User",
    ):
        assert parser.can_fetch(agent, page), agent
        assert not parser.can_fetch(agent, "https://thestill.test/inbox"), agent


def test_each_public_page_logs_one_served_line(site, app_state, monkeypatch):
    """Spec #105 §5: kind, status and crawler family; no IP, no user agent."""
    _seed(app_state)
    events = []

    class _Recorder:
        # Runs the real context merge, so fields bound by LoggingMiddleware
        # (request_id, client_ip, ...) show up exactly as the formatter sees them.
        def info(self, event, **fields):
            events.append((event, structlog.contextvars.merge_contextvars(None, "info", dict(fields))))

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    monkeypatch.setattr(public_shell, "logger", _Recorder())
    bing = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
    site.get("/podcasts/public-show/episodes/summarised", headers={"User-Agent": bing})
    site.get("/podcasts/unknown-show", headers={"User-Agent": "Mozilla/5.0 (Macintosh) Chrome/129.0 Safari/537.36"})
    site.get("/inbox")
    served = [fields for event, fields in events if event == "public_shell.served"]
    assert [(f["kind"], f["status"], f["ua_family"]) for f in served] == [
        ("episode", 200, "bingbot"),
        ("podcast", 404, "browser"),
    ]
    for fields in served:
        assert {"kind", "status", "ua_family", "ms", "request_id"} <= set(fields)
        assert "client_ip" not in fields and "user_agent" not in fields


def test_sitemap_lists_a_retyped_entity_over_an_empty_row(site, app_state, monkeypatch):
    """``company:mistral-ai`` is an empty leftover; the re-typed
    ``topic:mistral-ai`` (now a company) has the mentions, so the page shows
    it and the sitemap lists its URL."""
    from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
    from thestill.repositories import public_quotes

    _seed(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="company:mistral-ai", type=EntityType.COMPANY, canonical_name="Mistral AI"))
    repo.upsert_entity(
        EntityRecord(id="topic:mistral-ai", type=EntityType.COMPANY, canonical_name="Mistral AI", wikidata_qid="Q1")
    )
    repo.insert_mentions(
        [
            EntityMention(
                entity_id="topic:mistral-ai",
                episode_id="00000000-0000-0000-0000-000000000001",
                segment_id=1,
                start_ms=100_000,
                end_ms=104_000,
                role=MentionRole.MENTIONED,
                surface_form="Mistral",
                quote_excerpt="Mistral shipped a model.",
                confidence=0.9,
                extractor="gliner:test",
            )
        ]
    )
    rows = [{"entity_id": "topic:mistral-ai", "entity_type": "company", "quotes": 5}]
    monkeypatch.setattr(public_quotes.PublicQuoteQueries, "indexable_entities", lambda self, min_quotes=3: rows)
    body = site.get("/sitemap.xml").text
    assert body.count("https://thestill.test/entities/company/mistral-ai</loc>") == 1


def test_sitemap_skips_an_entity_whose_url_shows_another(site, app_state, monkeypatch):
    """Two entities can claim one URL after a re-type; the page shows the one
    with the URL's own prefix, so only that one may be listed."""
    from thestill.models.entities import EntityRecord, EntityType
    from thestill.repositories import public_quotes

    repo = app_state.entity_repository
    for entity_id, kind in (
        ("topic:islam", EntityType.TOPIC),
        ("person:islam", EntityType.TOPIC),  # a given name, re-typed to topic
        ("company:abraham-pais", EntityType.PERSON),  # re-typed, no rival
    ):
        repo.upsert_entity(EntityRecord(id=entity_id, type=kind, canonical_name=entity_id))
    rows = [
        {"entity_id": "person:islam", "entity_type": "topic", "quotes": 15},
        {"entity_id": "topic:islam", "entity_type": "topic", "quotes": 4},
        {"entity_id": "company:abraham-pais", "entity_type": "person", "quotes": 5},
    ]
    monkeypatch.setattr(public_quotes.PublicQuoteQueries, "indexable_entities", lambda self, min_quotes=3: rows)
    body = site.get("/sitemap.xml").text
    assert body.count("https://thestill.test/entities/topic/islam</loc>") == 1
    assert "https://thestill.test/entities/person/abraham-pais</loc>" in body
    # The loser is dropped, not listed under its stale prefix either.
    assert "/entities/person/islam" not in body


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


def test_person_page_tags_and_sitemap(site, app_state):
    from tests.integration.web.test_public_api import _seed_people

    _seed_people(app_state)
    response = site.get("/entities/person/scott-galloway")
    assert response.status_code == 200
    head = _head(response.text)
    assert _title(response.text).startswith("Scott Galloway — podcast quotes and appearances")
    # Two quotes is below the indexing threshold of three.
    assert 'name="robots" content="noindex"' in head
    assert site.get("/entities/person/hidden-person").status_code == 404
    assert site.get("/entities/planet/earth").status_code == 404


def test_signed_in_users_are_never_refused_the_shell(site, app_state, monkeypatch):
    _seed(app_state)
    monkeypatch.setattr(rate_limit, "PUBLIC_LIMIT", rate_limit.RateLimit(max_events=1, window_seconds=60))
    assert site.get("/podcasts/unknown-a").status_code == 404
    assert site.get("/podcasts/unknown-b").status_code == 429
    monkeypatch.setattr(app_state.auth_service, "get_current_user", lambda token: object() if token == "good" else None)
    site.cookies.set(AUTH_COOKIE_NAME, "good")
    assert site.get("/podcasts/unknown-c").status_code == 404
    site.cookies.set(AUTH_COOKIE_NAME, "forged")
    assert site.get("/podcasts/unknown-d").status_code == 429


def test_a_failed_lookup_is_503_for_crawlers_not_a_page(site, app_state, monkeypatch):
    _seed(app_state)

    def down(self, parts):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(public_shell.ShellRenderer, "_podcast_or_episode", down)
    response = site.get("/podcasts/public-show")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "120"
    assert "<!--thestill:head-->" in response.text or "<title>" in response.text
    # Signed-in app paths need no lookup and keep working.
    assert site.get("/inbox").status_code == 200
