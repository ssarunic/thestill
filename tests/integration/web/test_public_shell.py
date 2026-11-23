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


def test_claude_guide_is_indexable_and_in_the_sitemap(site, app_state):
    """Spec #108 — /claude is the connector's public documentation."""
    response = site.get("/claude")
    assert response.status_code == 200
    head = _head(response.text)
    assert _title(response.text) == "Use Thestill in Claude · Thestill"
    assert "noindex" not in head
    assert '<link rel="canonical" href="https://thestill.test/claude" />' in head
    assert "<loc>https://thestill.test/claude</loc>" in site.get("/sitemap.xml").text


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
    # Spec #105 search titles: what the page is, then the show; no site name.
    assert _title(episode.text) == "Episode 1 — Public Show summary"
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
    # The search title drops tags from the publisher's title altogether.
    assert "<" not in _title(head) and "alert(1)" in _title(head)
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', head, re.S).group(1)
    assert "</" not in ld and "\\u003c/script\\u003e" in ld


def test_root_files_are_served_as_files(site):
    response = site.get("/favicon.svg")
    assert response.status_code == 200
    assert response.text == "<svg/>"


def test_service_worker_is_served_uncached_and_never_as_the_shell(site, static_dir):
    """Spec #116 — a cached service worker cannot be withdrawn; and its URL
    must not fall through to index.html when the build has none."""
    assert site.get("/sw.js").status_code == 404
    assert site.get("/sw.js").headers["cache-control"] == "no-cache"

    (static_dir / "sw.js").write_text("// worker", encoding="utf-8")
    response = site.get("/sw.js")
    assert response.status_code == 200
    assert response.text == "// worker"
    assert response.headers["cache-control"] == "no-cache"
    assert "javascript" in response.headers["content-type"]
    # Other root files keep their day of caching.
    assert site.get("/favicon.svg").headers["cache-control"] == "public, max-age=86400"


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


def test_robots_keeps_entity_heavy_crawlers_off_entities_only(site):
    """Their own group drops /entities/ but keeps every private block of ``*``."""
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(site.get("/robots.txt").text.splitlines())
    base = "https://thestill.test"
    for agent in public_shell.ENTITY_HEAVY_CRAWLERS:
        for blocked in (
            "/entities/person/jane-doe",
            "/api/public/entities/person/jane-doe",
            "/inbox",
            "/api/inbox",
            "/login",
            "/mcp",
        ):
            assert not parser.can_fetch(agent, base + blocked), (agent, blocked)
        for allowed in ("/", "/top", "/podcasts/a/episodes/b", "/api/public/podcasts/a"):
            assert parser.can_fetch(agent, base + allowed), (agent, allowed)
    for agent in ("Googlebot", "Bingbot", "Applebot", "Amazonbot"):
        assert parser.can_fetch(agent, base + "/entities/person/jane-doe"), agent


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


def _facts(entity_id, entity_type, said=0, about=0, episodes=1, qid=None, instance_of="[]"):
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "said": said,
        "about": about,
        "episodes": episodes,
        "wikidata_qid": qid,
        "wikidata_instance_of": instance_of,
    }


PUBLIC_SHOW_ID = "11111111-1111-1111-1111-111111111111"


def test_sitemap_lists_a_retyped_entity_over_an_empty_row(site, app_state, monkeypatch):
    """``person:jane-doe`` is an empty leftover; the re-typed
    ``company:jane-doe`` (now a person, the show's host) has the mentions,
    so the page shows it and the sitemap lists its URL once."""
    from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole
    from thestill.repositories import public_quotes

    _seed(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:jane-doe", type=EntityType.PERSON, canonical_name="Jane Doe"))
    repo.upsert_entity(
        EntityRecord(id="company:jane-doe", type=EntityType.PERSON, canonical_name="Jane Doe", wikidata_qid="Q1")
    )
    repo.insert_mentions(
        [
            EntityMention(
                entity_id="company:jane-doe",
                episode_id="00000000-0000-0000-0000-000000000001",
                segment_id=1,
                start_ms=100_000,
                end_ms=104_000,
                role=MentionRole.MENTIONED,
                surface_form="Jane",
                quote_excerpt="Jane shipped a model.",
                confidence=0.9,
                extractor="gliner:test",
            )
        ]
    )
    repo.set_podcast_hosts(PUBLIC_SHOW_ID, ["company:jane-doe"])
    rows = [_facts("company:jane-doe", "person", about=12)]
    monkeypatch.setattr(public_quotes.PublicQuoteQueries, "entity_index_facts", lambda self: rows)
    body = site.get("/sitemap.xml").text
    assert body.count("https://thestill.test/entities/person/jane-doe</loc>") == 1


def test_sitemap_skips_an_entity_whose_url_shows_another(site, app_state, monkeypatch):
    """Two entities can claim one URL after a re-type; the page shows the one
    with the URL's own prefix, so only that one may be listed."""
    from thestill.models.entities import EntityRecord, EntityType
    from thestill.repositories import public_quotes

    _seed(app_state)
    repo = app_state.entity_repository
    for entity_id, kind in (
        ("person:ali", EntityType.PERSON),
        ("topic:ali", EntityType.PERSON),  # re-typed to a person, a rival for person/ali
        ("company:abraham-pais", EntityType.PERSON),  # re-typed, no rival
    ):
        repo.upsert_entity(EntityRecord(id=entity_id, type=kind, canonical_name=entity_id))
    repo.set_podcast_hosts(PUBLIC_SHOW_ID, ["person:ali", "topic:ali", "company:abraham-pais"])
    rows = [
        _facts("topic:ali", "person", said=15),
        _facts("person:ali", "person", said=10),
        _facts("company:abraham-pais", "person", said=5),
    ]
    monkeypatch.setattr(public_quotes.PublicQuoteQueries, "entity_index_facts", lambda self: rows)
    body = site.get("/sitemap.xml").text
    assert body.count("https://thestill.test/entities/person/ali</loc>") == 1
    assert "https://thestill.test/entities/person/abraham-pais</loc>" in body
    # The loser is dropped, not listed under its stale prefix either.
    assert "/entities/topic/ali" not in body


def test_sitemap_lists_only_people_with_a_role(site, app_state, monkeypatch):
    """Spec #105 decision 3 (2026-11-20): topics, companies, products and
    people only talked about stay out of the sitemap, however much they
    are discussed; a host is listed."""
    from thestill.models.entities import EntityRecord, EntityType
    from thestill.repositories import public_quotes

    _seed(app_state)
    repo = app_state.entity_repository
    for entity_id, kind in (
        ("person:the-host", EntityType.PERSON),
        ("person:only-mentioned", EntityType.PERSON),
        ("topic:chips", EntityType.TOPIC),
        ("company:nvidia", EntityType.COMPANY),
    ):
        repo.upsert_entity(EntityRecord(id=entity_id, type=kind, canonical_name=entity_id))
    repo.set_podcast_hosts(PUBLIC_SHOW_ID, ["person:the-host"])
    rows = [
        _facts("person:the-host", "person", said=1),
        _facts("person:only-mentioned", "person", said=20, about=40, episodes=9),
        _facts("topic:chips", "topic", about=200, episodes=30),
        _facts("company:nvidia", "company", about=90, episodes=12),
    ]
    monkeypatch.setattr(public_quotes.PublicQuoteQueries, "entity_index_facts", lambda self: rows)
    body = site.get("/sitemap.xml").text
    assert "https://thestill.test/entities/person/the-host</loc>" in body
    for absent in ("only-mentioned", "/entities/topic/", "/entities/company/"):
        assert absent not in body


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


def test_a_retyped_entity_moves_its_old_url_to_the_current_one(site, app_state):
    """``company:nvidia`` re-typed to a product answers at both URLs; the
    old type's URL is sent on so search sees one page, query kept."""
    from tests.integration.web.test_public_api import _seed_people
    from thestill.models.entities import EntityRecord, EntityType

    _seed_people(app_state)
    app_state.entity_repository.upsert_entity(
        EntityRecord(id="company:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia")
    )
    moved = site.get("/entities/company/nvidia?about=x&utm_source=a", follow_redirects=False)
    assert moved.status_code == 301
    # One hop: the legacy ?about= moves to the fragment on the way.
    assert moved.headers["location"] == "/entities/product/nvidia?utm_source=a#about=x"
    assert "no-cache" in moved.headers["cache-control"]
    current = site.get("/entities/product/nvidia", follow_redirects=False)
    assert current.status_code == 200
    assert 'rel="canonical" href="https://thestill.test/entities/product/nvidia"' in _head(current.text)


def test_an_undone_retype_never_loops_the_two_urls(site, app_state):
    """The re-type is undone and only the payload cache expires (the URL
    and shell caches are still warm): the company URL must answer itself,
    not a cached 301 to the product URL that now points back."""
    from tests.integration.web.test_public_api import _seed_people
    from thestill.models.entities import EntityRecord, EntityType
    from thestill.web.routes import api_public

    _seed_people(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="company:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia"))
    assert site.get("/entities/company/nvidia", follow_redirects=False).status_code == 301
    assert site.get("/entities/product/nvidia", follow_redirects=False).status_code == 200
    repo.upsert_entity(EntityRecord(id="company:nvidia", type=EntityType.COMPANY, canonical_name="Nvidia"))
    api_public._entity_cache.clear()  # noqa: SLF001 - the payload TTL running out
    assert site.get("/entities/company/nvidia", follow_redirects=False).status_code == 200
    # The product URL may serve its cached page until the shell TTL runs
    # out; if it redirects, it is to the company URL, which answers itself.
    back = site.get("/entities/product/nvidia", follow_redirects=False)
    assert back.status_code == 200 or back.headers["location"] == "/entities/company/nvidia"


def test_a_twice_retyped_entity_moves_a_url_at_neither_type(site, app_state):
    """``company:artificial-intelligence`` was a topic for a while (Google
    indexed ``topic/artificial-intelligence``) and is a product now. That
    URL matches neither the id prefix nor the current type; it is sent on
    rather than dropped as a 404 (Search Console 2026-10-26)."""
    from tests.integration.web.test_public_api import _seed_people
    from thestill.models.entities import EntityRecord, EntityType

    _seed_people(app_state)
    app_state.entity_repository.upsert_entity(
        EntityRecord(
            id="company:artificial-intelligence", type=EntityType.PRODUCT, canonical_name="Artificial intelligence"
        )
    )
    moved = site.get("/entities/topic/artificial-intelligence?about=x", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == "/entities/product/artificial-intelligence#about=x"
    assert "no-cache" in moved.headers["cache-control"]
    assert site.get("/entities/person/artificial-intelligence", follow_redirects=False).status_code == 301
    current = site.get("/entities/product/artificial-intelligence", follow_redirects=False)
    assert current.status_code == 200
    # A slug no stored id carries is still a 404 at every type.
    assert site.get("/entities/topic/twitter", follow_redirects=False).status_code == 404


def _merge_titled_guest(app_state):
    """``person:dr-sahar-yousef`` folded into ``person:sahar-yousef``, the
    titled-guest merge of 2026-11-19 that left its URL answering 404."""
    from tests.integration.web.test_public_api import _seed_people
    from thestill.models.entities import EntityRecord, EntityType

    _seed_people(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:sahar-yousef", type=EntityType.PERSON, canonical_name="Sahar Yousef"))
    repo.upsert_entity(
        EntityRecord(id="person:dr-sahar-yousef", type=EntityType.PERSON, canonical_name="Dr Sahar Yousef")
    )
    assert repo.delete_entity("person:dr-sahar-yousef", redirect_to="person:sahar-yousef")
    return repo


def test_a_merged_entity_moves_its_url_to_the_keeper(site, app_state):
    _merge_titled_guest(app_state)
    moved = site.get("/entities/person/dr-sahar-yousef?about=x", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == "/entities/person/sahar-yousef#about=x"
    assert site.get("/entities/person/sahar-yousef", follow_redirects=False).status_code == 200


def test_a_merged_entity_url_is_a_404_when_the_keeper_is_hidden_or_gone(site, app_state):
    from thestill.web.public_access import get_public_visibility

    repo = _merge_titled_guest(app_state)
    repo.set_entity_public_hidden("person:sahar-yousef", True)
    get_public_visibility(app_state).invalidate()
    assert site.get("/entities/person/dr-sahar-yousef", follow_redirects=False).status_code == 404
    repo.set_entity_public_hidden("person:sahar-yousef", False)
    repo.delete_entity("person:sahar-yousef")
    get_public_visibility(app_state).invalidate()
    assert site.get("/entities/person/dr-sahar-yousef", follow_redirects=False).status_code == 404


def test_a_live_row_wins_over_a_redirect(site, app_state):
    from thestill.models.entities import EntityRecord, EntityType

    repo = _merge_titled_guest(app_state)
    repo.upsert_entity(
        EntityRecord(id="person:dr-sahar-yousef", type=EntityType.PERSON, canonical_name="Dr Sahar Yousef")
    )
    assert site.get("/entities/person/dr-sahar-yousef", follow_redirects=False).status_code == 200


def test_a_retyped_entity_keeps_its_old_url_when_the_current_one_shows_another(site, app_state):
    """``product/nvidia`` belongs to a ``product:nvidia`` row with mentions of
    its own, so redirecting there would show a different entity."""
    from tests.integration.web.test_public_api import _seed_people
    from thestill.models.entities import EntityMention, EntityRecord, EntityType, MentionRole

    _seed_people(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="company:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia"))
    repo.upsert_entity(EntityRecord(id="product:nvidia", type=EntityType.PRODUCT, canonical_name="Nvidia Shield"))
    repo.insert_mentions(
        [
            EntityMention(
                entity_id="product:nvidia",
                episode_id="00000000-0000-0000-0000-000000000001",
                segment_id=9,
                start_ms=540_000,
                end_ms=544_000,
                role=MentionRole.MENTIONED,
                surface_form="Shield",
                quote_excerpt="The Shield is still on sale.",
                confidence=0.9,
                extractor="gliner:test",
            )
        ]
    )
    old = site.get("/entities/company/nvidia", follow_redirects=False)
    assert old.status_code == 200
    assert 'rel="canonical" href="https://thestill.test/entities/company/nvidia"' in _head(old.text)
    assert "Nvidia Shield" in _title(site.get("/entities/product/nvidia").text)


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


def test_served_pages_carry_no_template_comments(site, app_state):
    """Every comment in index.html ships in every page; only the markers
    the server replaces may be there (and an empty body pair)."""
    _seed(app_state)
    for path in ("/", "/podcasts/public-show/episodes/summarised", "/inbox"):
        comments = set(re.findall(r"<!--(.*?)-->", site.get(path).text, re.S))
        assert comments <= {"thestill:body", "/thestill:body"}, (path, comments)


def test_legacy_query_deep_links_move_to_the_fragment(site, app_state):
    """Google indexed ``?t=``/``?about=`` variants as pages of their own,
    splitting a page's ranking. A full load of one goes permanently to the
    clean URL, the value in the fragment, other parameters kept."""
    from tests.integration.web.test_public_api import _seed_people

    _seed(app_state)
    _seed_people(app_state)
    episode = "/podcasts/public-show/episodes/summarised"

    moved = site.get(f"{episode}?t=901", follow_redirects=False)
    assert moved.status_code == 301
    assert moved.headers["location"] == f"{episode}#t=901"
    assert moved.headers["cache-control"] == "public, max-age=86400"

    kept = site.get(f"{episode}?view=transcript&t=901.5", follow_redirects=False)
    assert kept.headers["location"] == f"{episode}?view=transcript#t=901.5"

    # A malformed value is dropped: the URL is still a duplicate.
    assert site.get(f"{episode}?t=soon", follow_redirects=False).headers["location"] == episode

    person = site.get("/entities/person/scott-galloway?about=nvidia", follow_redirects=False)
    assert person.status_code == 301
    assert person.headers["location"] == "/entities/person/scott-galloway#about=nvidia"

    # The clean URL itself answers, and a missing page stays a 404.
    assert site.get(episode, follow_redirects=False).status_code == 200
    assert site.get(f"{episode}?view=transcript", follow_redirects=False).status_code == 200
    assert site.get("/podcasts/public-show/episodes/nope?t=1", follow_redirects=False).status_code == 404


def test_search_titles_name_the_guest_and_describe_the_person(site, app_state):
    """An episode is titled by its guest when the publisher names them; a
    person's description says who they are, not a cut quote."""
    from tests.integration.web.test_public_api import _seed_people

    _seed_people(app_state)
    repo = app_state.entity_repository
    repo.set_podcast_hosts(PUBLIC_SHOW_ID, ["person:scott-galloway"])
    repo.set_episode_guests("00000000-0000-0000-0000-000000000001", ["person:scott-galloway", "person:ed-elson"])
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute(
            "UPDATE episodes SET title = ?, description = ? WHERE slug = 'summarised'",
            ("Why chips are the new oil", "Ed Elson joins to argue about chips."),
        )
    title = _title(site.get("/podcasts/public-show/episodes/summarised").text)
    # The host is not a guest; the guest and the topic lead.
    assert title == "Ed Elson: Why chips are the new oil — Public Show"
    head = _head(site.get("/entities/person/scott-galloway").text)
    description = re.search(r'<meta name="description" content="(.*?)"', head).group(1)
    assert description.startswith("Scott Galloway, host of Public Show.")
    assert "“" not in description  # no cut quote


def test_two_episodes_with_the_same_guest_get_different_titles(site, app_state):
    """A recurring guest without a topic that fits would give every episode
    "<Guest> on <Show> — summary & key quotes"; within a show, titles are unique."""
    from tests.integration.web.test_public_api import _seed_people

    _seed_people(app_state)
    repo = app_state.entity_repository
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute(
            "UPDATE episodes SET title = 'Ep 1', description = 'Ed Elson joins.', summary_preview = 'A.' "
            "WHERE id = '00000000-0000-0000-0000-000000000001'"
        )
        conn.execute(
            "UPDATE episodes SET title = 'Ep 2', description = 'Ed Elson again.', summary_preview = 'B.', "
            "summary_path = (SELECT summary_path FROM episodes WHERE id = '00000000-0000-0000-0000-000000000001') "
            "WHERE id = '00000000-0000-0000-0000-000000000002'"
        )
    for episode_id in ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"):
        repo.set_episode_guests(episode_id, ["person:ed-elson"])
    first = _title(site.get("/podcasts/public-show/episodes/summarised").text)
    second = _title(site.get("/podcasts/public-show/episodes/no-summary").text)
    assert first.startswith("Ed Elson") and second.startswith("Ed Elson")
    assert first != second


@pytest.mark.parametrize("status", [None, "pending", "failed", "skipped_unavailable"])
def test_an_episode_without_its_entities_is_readable_but_not_indexed(site, app_state, status):
    """Summarised but entities not in: the page works, carries noindex and
    is left out of the sitemap until extraction settles."""
    _seed(app_state)
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE episodes SET entity_extraction_status = ? WHERE slug = 'summarised'", (status,))
    response = site.get("/podcasts/public-show/episodes/summarised")
    assert response.status_code == 200
    assert 'name="robots" content="noindex"' in _head(response.text)
    assert "/episodes/summarised" not in site.get("/sitemap.xml").text


@pytest.mark.parametrize("status", ["complete", "linking_deferred", "skipped_legacy"])
def test_an_episode_with_settled_entities_is_indexed(site, app_state, status):
    _seed(app_state)
    app_state.repository.update_entity_extraction_status(
        episode_id="00000000-0000-0000-0000-000000000001", status=status
    )
    head = _head(site.get("/podcasts/public-show/episodes/summarised").text)
    assert 'content="noindex"' not in head
    assert "/episodes/summarised" in site.get("/sitemap.xml").text
