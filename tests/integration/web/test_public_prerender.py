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

"""Spec #105 — server-written content for public pages.

The rules under test: every user agent gets the same bytes; the fallback
says nothing the page does not (subset parity); the content budget holds
(summary lead yes, held-back sections and transcripts no, at most 6
quotes); everything is escaped; signed-in requests, the runtime switch, the
concurrency cap and a failing builder all fall back to the head-only shell.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from thestill.services.abuse.actor import Actor
from thestill.web import public_prerender
from thestill.web.dependencies import AUTH_COOKIE_NAME

from .test_public_api import _fresh_buckets, _seed, _seed_people, _write_summary, app_config  # noqa: F401
from .test_public_shell import site, static_dir  # noqa: F401

EPISODE = "/podcasts/public-show/episodes/summarised"
BING = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
USER_AGENTS = [
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    BING,
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "",
]


class _Page(HTMLParser):
    """Text nodes, hrefs and the data block of the server-written root."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_root = 0
        self.depth = 0
        self.time_depth = 0
        self.in_bootstrap = False
        self.texts: List[str] = []
        self.hrefs: List[str] = []
        self.tags: List[str] = []
        self.bootstrap: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "root":
            self.in_root = 1
            self.depth = 0
            return
        if not self.in_root:
            return
        self.depth += 1
        self.tags.append(tag)
        if tag == "time":
            self.time_depth += 1
        if tag == "a":
            self.hrefs.append(attrs.get("href") or "")
        if tag == "script" and attrs.get("id") == public_prerender.BOOTSTRAP_ID:
            self.in_bootstrap = True

    def handle_endtag(self, tag):
        if not self.in_root:
            return
        if self.depth == 0:
            self.in_root = 0
            return
        self.depth -= 1
        if tag == "time":
            self.time_depth -= 1
        if tag == "script":
            self.in_bootstrap = False

    def handle_data(self, data):
        if not self.in_root:
            return
        if self.in_bootstrap:
            self.bootstrap = (self.bootstrap or "") + data
        elif not self.time_depth and data.strip():
            self.texts.append(data)


def _parse(html_text: str) -> _Page:
    page = _Page()
    page.feed(html_text.split("<body>", 1)[1])
    return page


def _bootstrap(page: _Page) -> Dict[str, Any]:
    assert page.bootstrap is not None, "no data block"
    return json.loads(page.bootstrap)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [_norm(value)]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _assert_subset(page: _Page) -> None:
    """Every text node is a payload string (or part of one), a UI string
    or a derived count/timestamp."""
    payload_strings = _strings([q["data"] for q in _bootstrap(page)["queries"]])
    for raw in page.texts:
        # Separators share a text node with the words around them.
        for part in _norm(raw).split("·"):
            node = part.strip().strip("“”").removesuffix("…").strip()
            if not node or node in public_prerender.UI_STRINGS:
                continue
            if any(p.match(node) for p in public_prerender.UI_PATTERNS):
                continue
            assert any(node in s for s in payload_strings), f"text not on the page: {node!r}"


def _root(html_text: str) -> str:
    return html_text.split('<div id="root">', 1)[1].split("</div>\n", 1)[0]


def _marker_is_empty(html_text: str) -> bool:
    return '<div id="root"><!--thestill:body--><!--/thestill:body--></div>' in html_text


def _add_quotes(app_state, count: int) -> None:
    """``count`` more quotes by Scott Galloway in episode 1, a minute apart."""
    from thestill.models.entities import EntityMention, MentionRole

    mentions = []
    for i in range(count):
        segment = 20 + i
        common = dict(
            episode_id="00000000-0000-0000-0000-000000000001",
            segment_id=segment,
            start_ms=segment * 60_000,
            end_ms=segment * 60_000 + 20_000,
            speaker="Scott Galloway",
            confidence=1.0,
        )
        mentions.append(
            EntityMention(
                entity_id="person:scott-galloway",
                role=MentionRole.SPEAKING,
                surface_form="Scott Galloway",
                quote_excerpt="First sentence only.",
                extractor="speaker:synth",
                **common,
            )
        )
        mentions.append(
            EntityMention(
                entity_id="company:nvidia",
                role=MentionRole.MENTIONED,
                surface_form="Nvidia",
                quote_excerpt=f"Extra quote number {i} says Nvidia shipped more chips than anyone expected this year.",
                extractor="gliner:test",
                **common,
            )
        )
    app_state.entity_repository.insert_mentions(mentions)
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE entity_mentions SET resolution_status = 'resolved' WHERE entity_id IS NOT NULL")


# ---------------------------------------------------------------------------
# Episode pages (Phase 1)
# ---------------------------------------------------------------------------


def test_episode_page_has_its_content_in_the_html(site, app_state):
    _seed_people(app_state)
    response = site.get(EPISODE, headers={"User-Agent": BING})
    assert response.status_code == 200
    page = _parse(response.text)
    text = " ".join(_norm(t) for t in page.texts)
    # Key blocks: the h1, the summary lead's first sentence, the first quote.
    assert page.tags.count("h1") == 1
    assert "Episode 1" in text
    assert "Two hosts argue about chips." in text
    assert "I think Nvidia has built the deepest moat" in text
    assert "Scott Galloway" in text and "Companies mentioned" in text
    assert "Publisher notes." in text
    bootstrap = _bootstrap(page)
    assert bootstrap["v"] == public_prerender.BOOTSTRAP_VERSION
    assert bootstrap["page"] == "episode" and bootstrap["viewer"] == "anonymous"
    assert [q["query"] for q in bootstrap["queries"]] == ["episode", "summary", "episodeQuotes", "podcastEpisodes"]


def test_every_user_agent_gets_the_same_bytes(site, app_state):
    _seed_people(app_state)
    bodies = {site.get(EPISODE, headers={"User-Agent": ua}).text for ua in USER_AGENTS}
    assert len(bodies) == 1


def test_fallback_text_is_a_subset_of_the_page(site, app_state):
    _seed_people(app_state)
    _add_quotes(app_state, 3)
    page = _parse(site.get(EPISODE).text)
    assert len(page.texts) > 20
    _assert_subset(page)


def test_bootstrap_payloads_match_the_api(site, app_state):
    _seed_people(app_state)
    page = _parse(site.get(EPISODE).text)
    by_query = {q["query"]: q for q in _bootstrap(page)["queries"]}
    api = "/api/public" + EPISODE
    for name, url in (
        ("episode", api),
        ("summary", api + "/summary"),
        ("episodeQuotes", api + "/quotes?page=1"),
        ("podcastEpisodes", "/api/public/podcasts/public-show/episodes?limit=10&offset=0"),
    ):
        body = site.get(url).json()
        body.pop("status"), body.pop("timestamp")
        assert by_query[name]["data"] == body, name
    assert by_query["episodeQuotes"]["page_param"] == 1
    assert by_query["podcastEpisodes"]["page_param"] == 0


def test_budget_keeps_held_back_sections_transcripts_and_extra_quotes_out(site, app_state):
    _seed_people(app_state)
    _add_quotes(app_state, 8)
    long_description = " ".join(f"word{i}" for i in range(200))
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE episodes SET description = ? WHERE slug = 'summarised'", (long_description,))
    _write_summary(
        app_state,
        "summarised_summary.md",
        "## The Gist\n\nTwo hosts argue about chips.\n\n## Timeline\n\n- [01:00] Secret timeline line\n\n"
        "## Drama\n\nSecret drama line.\n",
    )
    response = site.get(EPISODE)
    root = _root(response.text)
    assert "Secret timeline line" not in root and "Secret drama line" not in root
    page = _parse(response.text)
    assert page.tags.count("figure") == public_prerender_quote_page()
    assert "First sentence only." not in root  # speaking-row excerpts are not quotes
    description = next(t for t in page.texts if t.startswith("word0"))
    assert len(description.split()) == 80 and description.endswith("…")
    quotes = next(q for q in _bootstrap(page)["queries"] if q["query"] == "episodeQuotes")
    assert len(quotes["data"]["quotes"]) == 6 and quotes["data"]["next_page"] == 2


def public_prerender_quote_page() -> int:
    from thestill.web.routes import api_public

    return api_public._EPISODE_QUOTE_PAGE  # noqa: SLF001 - the budget under test


@pytest.mark.parametrize("query", ["?speaker=scott-galloway", "?view=transcript", "?about=nvidia"])
def test_a_query_string_gets_only_the_header_block(site, app_state, query):
    _seed_people(app_state)
    page = _parse(site.get(EPISODE + query).text)
    text = " ".join(page.texts)
    assert page.tags.count("h1") == 1 and "Episode 1" in text
    assert "Two hosts argue" not in text and "deepest moat" not in text
    assert [q["query"] for q in _bootstrap(page)["queries"]] == ["episode"]
    _assert_subset(page)


def test_untrusted_text_comes_out_inert(site, app_state):
    _seed_people(app_state)
    with app_state.repository._get_connection() as conn:  # noqa: SLF001 - fixture write
        conn.execute("UPDATE podcasts SET title = ? WHERE slug = 'public-show'", ("</h1><script>alert(1)</script>",))
        conn.execute(
            "UPDATE entity_mentions SET quote_excerpt = ? WHERE quote_excerpt LIKE 'I think Nvidia%'",
            ("Nvidia </script><script>alert(2)</script> is a long enough quote to keep.",),
        )
    _write_summary(
        app_state,
        "summarised_summary.md",
        "## The Gist\n\nTwo hosts <img src=x onerror=alert(3)> argue. [click](javascript:alert(4)) "
        "[data](data:text/html,x) [ok](https://example.com/a) [cite](?cite=c1)\n",
    )
    response = site.get(EPISODE)
    root = _root(response.text)
    assert "<script>alert" not in root and "<img" not in root
    assert "javascript:" not in root.split(public_prerender.BOOTSTRAP_ID)[0]
    page = _parse(response.text)
    assert "click" in " ".join(page.texts) and "cite" in " ".join(page.texts)
    assert "https://example.com/a" in page.hrefs
    # The data block round-trips: the escaping changed bytes, not values.
    quotes = next(q for q in _bootstrap(page)["queries"] if q["query"] == "episodeQuotes")
    assert any("</script><script>alert(2)" in q["text"] for q in quotes["data"]["quotes"])
    assert "</script><script>alert(2)" not in page.bootstrap


def test_links_are_canonical(site, app_state):
    _seed_people(app_state)
    page = _parse(site.get(EPISODE).text)
    internal = re.compile(
        r"^/(?:|top|terms|privacy|podcasts/[a-z0-9-]+(?:/episodes/[a-z0-9-]+(?:#t=\d+)?)?|entities/[a-z]+/[a-z0-9-]+)$"
    )
    for href in page.hrefs:
        assert "?t=" not in href and "?page=" not in href, href
        assert internal.match(href) or href == "/login" or href.startswith("https://"), href


def test_sign_in_links_share_one_url(site, app_state):
    """Every page's sign-in link is the bare ``/login`` with ``rel=nofollow``:
    one robots-blocked URL for the whole site, not ``/login?next=`` per page
    (Search Console listed 5,851 of those on 2026-10-26)."""
    _seed_people(app_state)
    for path in (EPISODE, "/podcasts/public-show", "/entities/person/scott-galloway"):
        html_text = site.get(path).text
        assert "/login?" not in html_text, path
        assert re.search(r'<a href="/login" rel="nofollow"', html_text), path


def test_signed_in_requests_keep_the_head_only_shell(site, app_state, monkeypatch):
    _seed_people(app_state)
    monkeypatch.setattr(app_state.auth_service, "get_current_user", lambda token: object() if token == "good" else None)
    site.cookies.set(AUTH_COOKIE_NAME, "good")
    response = site.get(EPISODE)
    assert response.status_code == 200
    assert _marker_is_empty(response.text)
    assert response.headers["Vary"].startswith("Cookie, Authorization")
    # A junk cookie is no session: that request gets the content.
    site.cookies.set(AUTH_COOKIE_NAME, "forged")
    assert 'data-prerendered="episode"' in site.get(EPISODE).text


def test_hidden_and_unsummarised_pages_get_no_content(site, app_state):
    _seed_people(app_state)
    for path in ("/podcasts/hidden-show/episodes/hidden-episode", "/podcasts/public-show/episodes/not-applicable"):
        response = site.get(path)
        assert response.status_code == 404, path
        assert _marker_is_empty(response.text), path


class _Recorder:
    def __init__(self) -> None:
        self.events: List[tuple] = []

    def info(self, event, **fields):
        self.events.append(("info", event, fields))

    def warning(self, event, **fields):
        self.events.append(("warning", event, fields))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_over_the_cap_a_miss_is_shed_to_the_head_only_shell(site, app_state, monkeypatch):
    _seed_people(app_state)
    recorder = _Recorder()
    monkeypatch.setattr(public_prerender, "logger", recorder)
    prerenderer = public_prerender.get_prerenderer(app_state)
    prerenderer._slot_wait = 0.0  # noqa: SLF001 - shed at once, as with every slot held past the wait
    held = 0
    while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001 - hold every slot
        held += 1
    try:
        response = site.get(EPISODE)
    finally:
        for _ in range(held):
            prerenderer._slots.release()  # noqa: SLF001
    assert response.status_code == 200 and _marker_is_empty(response.text)
    assert ("info", "public_prerender.shed", {"kind": "episode"}) in recorder.events
    # With a free slot the same page renders, and is then served from cache
    # even when every slot is taken.
    assert 'data-prerendered="episode"' in site.get(EPISODE).text
    held = 0
    while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001
        held += 1
    try:
        assert 'data-prerendered="episode"' in site.get(EPISODE).text
    finally:
        for _ in range(held):
            prerenderer._slots.release()  # noqa: SLF001


def test_the_runtime_switch_turns_it_off_without_a_restart(site, app_state, app_config):  # noqa: F811
    from thestill.repositories.factory import make_repositories
    from thestill.services.abuse.wiring import build_abuse_services

    app_state.abuse = build_abuse_services(app_config, make_repositories(app_config))
    _seed_people(app_state)
    assert 'data-prerendered="episode"' in site.get(EPISODE).text
    app_state.abuse.settings.set("public_prerender_enabled", False, actor=Actor.cli())
    assert _marker_is_empty(site.get(EPISODE).text)
    app_state.abuse.settings.reset("public_prerender_enabled", actor=Actor.cli())
    assert 'data-prerendered="episode"' in site.get(EPISODE).text


def test_a_failing_builder_serves_the_head_only_shell(site, app_state, monkeypatch):
    _seed_people(app_state)
    recorder = _Recorder()
    monkeypatch.setattr(public_prerender, "logger", recorder)

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(public_prerender, "episode_fallback", broken)
    response = site.get(EPISODE)
    assert response.status_code == 200 and _marker_is_empty(response.text)
    assert "<title>Episode 1" in response.text
    assert any(event == "public_prerender.failed" for _, event, _ in recorder.events)


def test_served_line_says_whether_the_page_was_prerendered(site, app_state, monkeypatch):
    from thestill.web import public_shell

    _seed_people(app_state)
    recorder = _Recorder()
    monkeypatch.setattr(public_shell, "logger", recorder)
    site.get(EPISODE)
    site.get(EPISODE)
    site.get("/podcasts/nope/episodes/nothing")
    served = [fields for _, event, fields in recorder.events if event == "public_shell.served"]
    assert [(f["status"], f["prerendered"], f["cache"]) for f in served] == [
        (200, "true", "miss"),
        (200, "true", "hit"),
        (404, "false", None),
    ]


def test_response_stays_within_the_size_budget(site, app_state):
    import gzip

    _seed_people(app_state)
    _add_quotes(app_state, 40)
    body = site.get(EPISODE).content
    assert len(body) <= 60_000
    assert len(gzip.compress(body)) <= 15_000


def test_an_old_build_without_the_marker_still_works(site, app_state, static_dir: Path):
    _seed_people(app_state)
    index = static_dir / "index.html"
    index.write_text(index.read_text().replace("<!--thestill:body--><!--/thestill:body-->", ""))
    response = site.get(EPISODE)
    assert response.status_code == 200 and '<div id="root"></div>' in response.text


@pytest.mark.parametrize("path", [EPISODE, "/podcasts/public-show"])
def test_a_shed_request_builds_no_payload(site, app_state, monkeypatch, path):
    """Over the cap, the page costs its head lookup only: every payload
    build happens under a slot (review of PR #409)."""
    from thestill.web.routes import api_public

    _seed_people(app_state)
    built: List[str] = []
    for name in ("_build_podcast_payload", "_build_episode_payload", "_build_summary_payload", "_episode_quote_set"):
        real = getattr(api_public, name)
        monkeypatch.setattr(
            api_public, name, lambda *a, _real=real, _name=name, **k: (built.append(_name), _real(*a, **k))[1]
        )
    prerenderer = public_prerender.get_prerenderer(app_state)
    prerenderer._slot_wait = 0.0  # noqa: SLF001 - shed at once, as with every slot held past the wait
    held = 0
    while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001 - hold every slot
        held += 1
    try:
        response = site.get(path)
    finally:
        for _ in range(held):
            prerenderer._slots.release()  # noqa: SLF001
    assert response.status_code == 200 and _marker_is_empty(response.text)
    assert "Public Show" in response.text.split("</head>", 1)[0]
    assert built == []


def test_over_the_cap_a_miss_waits_for_a_slot_before_shedding(site, app_state, monkeypatch):
    """A crawler's ordinary concurrency must not read as 5xx: a request that
    finds every slot taken waits for one, and renders once it frees up."""
    import threading

    _seed_people(app_state)
    recorder = _Recorder()
    monkeypatch.setattr(public_prerender, "logger", recorder)
    prerenderer = public_prerender.get_prerenderer(app_state)
    prerenderer._slot_wait = 5.0  # noqa: SLF001
    held = 0
    while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001 - hold every slot
        held += 1
    release_one = threading.Timer(0.3, prerenderer._slots.release)  # noqa: SLF001
    release_one.start()
    try:
        response = site.get("/entities/person/scott-galloway")
    finally:
        release_one.join()
        for _ in range(held - 1):
            prerenderer._slots.release()  # noqa: SLF001
    assert response.status_code == 200
    assert 'data-prerendered="entity"' in response.text
    assert not [e for e in recorder.events if e[1] == "public_prerender.shed"]


def test_an_uncached_entity_page_over_the_cap_is_a_503_not_a_build(site, app_state, monkeypatch):
    """The entity head needs the full entity payload (quote counts decide
    noindex); uncached and over the cap, it is shed, never built."""
    from thestill.web.routes import api_public

    _seed_people(app_state)
    built: List[str] = []
    real = api_public._build_entity_payload
    monkeypatch.setattr(api_public, "_build_entity_payload", lambda *a, **k: (built.append("entity"), real(*a, **k))[1])
    prerenderer = public_prerender.get_prerenderer(app_state)
    prerenderer._slot_wait = 0.0  # noqa: SLF001 - shed at once, as with every slot held past the wait
    held = 0
    while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001 - hold every slot
        held += 1
    try:
        shed = site.get("/entities/person/scott-galloway")
        assert shed.status_code == 503 and shed.headers["Retry-After"] == "120"
        assert built == []
        # Once cached, the head costs nothing and is served even at the cap.
        for _ in range(held):
            prerenderer._slots.release()  # noqa: SLF001
        held = 0
        assert site.get("/entities/person/scott-galloway").status_code == 200
        while prerenderer._slots.acquire(blocking=False):  # noqa: SLF001
            held += 1
        assert site.get("/entities/person/scott-galloway").status_code == 200
        assert built == ["entity"]
    finally:
        for _ in range(held):
            prerenderer._slots.release()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Podcast and entity pages (Phase 2)
# ---------------------------------------------------------------------------

SHOW = "/podcasts/public-show"


def _api_body(site, url: str) -> Dict[str, Any]:
    body = site.get(url).json()
    body.pop("status"), body.pop("timestamp")
    return body


def test_podcast_page_has_its_content_in_the_html(site, app_state):
    _seed_people(app_state)
    response = site.get(SHOW, headers={"User-Agent": BING})
    assert response.status_code == 200
    page = _parse(response.text)
    text = " ".join(_norm(t) for t in page.texts)
    assert page.tags.count("h1") == 1 and "Public Show" in text
    assert "A show about chips." in text
    assert "Latest episodes" in text and "Episode 1" in text and "Two hosts argue about chips." in text
    by_query = {q["query"]: q for q in _bootstrap(page)["queries"]}
    assert by_query["podcast"]["data"] == _api_body(site, "/api/public/podcasts/public-show")
    assert by_query["podcastEpisodes"]["data"] == _api_body(
        site, "/api/public/podcasts/public-show/episodes?limit=10&offset=0"
    )
    _assert_subset(page)


@pytest.mark.parametrize("path", ["/entities/person/scott-galloway", "/entities/company/nvidia"])
def test_entity_pages_have_their_content_in_the_html(site, app_state, path):
    _seed_people(app_state)
    response = site.get(path, headers={"User-Agent": BING})
    assert response.status_code == 200
    page = _parse(response.text)
    text = " ".join(_norm(t) for t in page.texts)
    payload = _api_body(site, "/api/public" + path)
    assert page.tags.count("h1") == 1 and payload["entity"]["name"] in text
    assert payload["featured_quote"]["text"] in text
    by_query = {(q["query"], tuple(q["args"][2:3])): q for q in _bootstrap(page)["queries"]}
    assert by_query[("entity", ())]["data"] == payload
    if path.startswith("/entities/person"):
        assert "What they said" in text
        said = _api_body(site, f"/api/public{path}/quotes?kind=said&page=1")
        assert by_query[("entityQuotes", ("said",))]["data"] == said
    else:
        assert "Who's talking" in text and "Latest on" in text
    _assert_subset(page)


@pytest.mark.parametrize("path", [SHOW, "/entities/person/scott-galloway", "/entities/company/nvidia"])
def test_podcast_and_entity_pages_are_ua_blind(site, app_state, path):
    _seed_people(app_state)
    assert len({site.get(path, headers={"User-Agent": ua}).text for ua in USER_AGENTS}) == 1


@pytest.mark.parametrize("path", [SHOW + "?x=1", "/entities/company/nvidia?about=x"])
def test_podcast_and_entity_query_strings_get_the_header_block(site, app_state, path):
    _seed_people(app_state)
    page = _parse(site.get(path).text)
    assert page.tags.count("h1") == 1
    assert len(_bootstrap(page)["queries"]) == 1
    assert "Latest episodes" not in " ".join(page.texts) and "Who's talking" not in " ".join(page.texts)


def test_a_hidden_show_leaves_the_entity_fallbacks_on_the_next_refresh(site, app_state):
    from thestill.web.public_access import get_public_visibility

    _seed_people(app_state)
    assert "deepest moat" in _root(site.get("/entities/company/nvidia").text)
    app_state.repository.set_podcast_public_pages("11111111-1111-1111-1111-111111111111", False)
    # The hidden sets refresh every 60 s; invalidate() is that refresh.
    get_public_visibility(app_state).invalidate()
    root = _root(site.get("/entities/company/nvidia").text)
    assert "deepest moat" not in root and "Public Show" not in root
    assert site.get(EPISODE).status_code == 404


def test_entity_noindex_list_keeps_a_page_out_of_search(site, app_state, app_config):  # noqa: F811
    from thestill.repositories.factory import make_repositories
    from thestill.services.abuse.wiring import build_abuse_services
    from thestill.web.public_access import EntityIndexFacts, entity_is_indexable

    app_state.abuse = build_abuse_services(app_config, make_repositories(app_config))
    _seed_people(app_state)
    _add_quotes(app_state, 6)  # Scott Galloway now has 7 quotes said
    head = site.get("/entities/person/scott-galloway").text.split("</head>", 1)[0]
    assert "noindex" not in head
    assert "/entities/person/scott-galloway</loc>" in site.get("/sitemap.xml").text
    app_state.abuse.settings.set("entity_noindex", ["person:scott-galloway"], actor=Actor.cli())
    from thestill.web.public_access import clear_public_caches

    clear_public_caches()
    head = site.get("/entities/person/scott-galloway").text.split("</head>", 1)[0]
    assert 'name="robots" content="noindex"' in head
    assert "/entities/person/scott-galloway</loc>" not in site.get("/sitemap.xml").text
    assert not entity_is_indexable(
        EntityIndexFacts("person:scott-galloway", "person", 7, 0, 1, False), ["person:scott-galloway"]
    )


def test_a_host_with_no_mentions_is_indexed_and_listed(site, app_state):
    """A role qualifies a page on its own, so the sitemap must consider
    role-bearing entities with no transcript mentions (review of #410)."""
    from thestill.models.entities import EntityRecord, EntityType

    _seed(app_state)
    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:quiet-host", type=EntityType.PERSON, canonical_name="Quiet Host"))
    repo.set_podcast_hosts("11111111-1111-1111-1111-111111111111", ["person:quiet-host"])
    page = site.get("/api/public/entities/person/quiet-host").json()
    assert page["indexable"] is True and page["roles"]["hosts"]
    assert "https://thestill.test/entities/person/quiet-host</loc>" in site.get("/sitemap.xml").text
    # Hidden, it is neither.
    repo.set_entity_public_hidden("person:quiet-host", True)
    from thestill.web.public_access import get_public_visibility

    get_public_visibility(app_state).invalidate()
    assert "quiet-host" not in site.get("/sitemap.xml").text


# ---------------------------------------------------------------------------
# Landing and top (Phase 3)
# ---------------------------------------------------------------------------


def test_landing_has_the_latest_summary_and_fresh_links(site, app_state):
    _seed_people(app_state)
    response = site.get("/", headers={"User-Agent": BING})
    page = _parse(response.text)
    text = " ".join(_norm(t) for t in page.texts)
    assert page.tags.count("h1") == 1 and "Read your podcasts." in text
    assert "Latest summary" in text and "Two hosts argue about chips." in text
    assert "Recently summarised" in text and EPISODE in page.hrefs
    by_query = {(q["query"], tuple(q["args"])): q["data"] for q in _bootstrap(page)["queries"]}
    assert by_query[("recent", (1,))] == _api_body(site, "/api/public/recent-summaries?limit=1")
    assert by_query[("recent", (6,))] == _api_body(site, "/api/public/recent-summaries?limit=6")
    assert by_query[("whoSaidWhat", ())] == _api_body(site, "/api/public/who-said-what")
    assert by_query[("summary", ("public-show", "summarised"))]["content"].startswith("## ")
    _assert_subset(page)
    assert len({site.get("/", headers={"User-Agent": ua}).text for ua in USER_AGENTS}) == 1


def test_top_shows_the_chart_for_the_visitors_region(site, app_state, monkeypatch):
    import sqlite3

    from thestill.web.routes import api_public

    from .conftest import seed_top_chart

    seed_top_chart(
        app_state,
        "gb",
        [
            {"rank": 1, "name": "The Rest Is History", "artist": "Goalhanger", "rss_url": "https://example.com/t"},
            {"rank": 2, "name": "Chart Two", "artist": "Someone", "rss_url": "https://example.com/u"},
        ],
    )
    with sqlite3.connect(str(app_state.repository.db_path)) as conn:
        conn.execute(
            "INSERT INTO top_podcasts_meta (region, source_path, source_mtime, row_count, seeded_at) "
            "VALUES ('us', 'test', 0, 0, '2026-10-15T00:00:00+00:00')"
        )
    lookups: list = []

    async def from_ip(ip):
        lookups.append(ip)
        return {"81.2.69.142": "gb"}.get(ip)

    monkeypatch.setattr(api_public, "lookup_country_from_ip", from_ip)
    monkeypatch.setattr(api_public, "_visitor_country_cache", api_public.TtlCache(60.0))
    monkeypatch.setattr(api_public, "resolve_client_ip", lambda request: request.headers.get("X-Test-Ip", ""))

    uk = _parse(site.get("/top", headers={"X-Test-Ip": "81.2.69.142"}).text)
    text = " ".join(_norm(t) for t in uk.texts)
    assert "Top podcasts" in text and "The Rest Is History" in text and "Goalhanger" in text
    (entry,) = _bootstrap(uk)["queries"]
    assert entry["query"] == "top" and entry["args"] == [None, None]
    assert entry["data"]["region"] == "gb"
    _assert_subset(uk)
    # The same request to the API answers with the same chart.
    body = site.get("/api/public/top-podcasts", headers={"X-Test-Ip": "81.2.69.142"}).json()
    body.pop("status"), body.pop("timestamp")
    assert entry["data"] == body
    assert lookups == ["81.2.69.142"]  # one lookup, cached for the API call
    # A visitor elsewhere gets the US chart (empty here), as the page would.
    us = _parse(site.get("/top", headers={"X-Test-Ip": "198.51.100.7"}).text)
    assert _bootstrap(us)["queries"][0]["data"]["region"] == "us"
    # ?region= is a filtered view: header only, nothing seeded.
    filtered = _parse(site.get("/top?region=gb", headers={"X-Test-Ip": "81.2.69.142"}).text)
    assert filtered.tags.count("h1") == 1 and _bootstrap(filtered)["queries"] == []
