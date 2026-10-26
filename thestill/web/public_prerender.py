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

"""Server-written page content for signed-out requests (spec #105).

For an anonymous request to a public content path, the shell's
``<!--thestill:body-->`` marker inside ``#root`` is replaced with semantic
HTML (headings, the summary lead, the first page of quotes, people and
internal links) and a ``<script type="application/json">`` data block. A
crawler that does not run JavaScript reads the HTML; the app seeds React
Query from the data block and ``createRoot`` replaces the HTML on mount (no
hydration).

Rules (spec #105 §2-3):

- **UA-blind.** Nothing here reads ``User-Agent``; every client gets the
  same bytes.
- **Subset.** Every text node is a payload string the page renders, a
  short derived value (a count, a timestamp, a ``<time>`` date) or one of
  :data:`UI_STRINGS`. Tests hold the fallback to that, so a new UI string is
  a deliberate change here.
- **One source.** HTML and data block come from the same payload objects as
  the ``/api/public`` routes, in the same request.
- **Escaped.** Every text and attribute value goes through ``html.escape``;
  summary Markdown renders with raw HTML off, and only ``http``, ``https``
  and site-relative links survive.
"""

from __future__ import annotations

import html
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException
from markdown_it import MarkdownIt
from structlog import get_logger

from .public_access import CacheMiss, cache_only
from .public_shell import safe_json, shell_kind

if TYPE_CHECKING:
    from fastapi import Request

    from .dependencies import AppState

logger = get_logger(__name__)

# The empty pair inside ``#root`` in ``index.html``: replaced for signed-out
# requests to a public page, left empty otherwise.
BODY_MARKER_RE = re.compile(r"<!--thestill:body-->.*?<!--/thestill:body-->", re.S)
BOOTSTRAP_ID = "thestill-bootstrap"
#: Format version of the data block; the app ignores any other.
BOOTSTRAP_VERSION = 1

#: Page kinds that get server-written content.
PRERENDER_KINDS = frozenset({"episode", "podcast", "entity", "landing", "top"})

_DESCRIPTION_WORDS = 80
_MORE_FROM = 3
_WITH_PEOPLE = 4
_GROUP_VISIBLE = 8

#: Fixed UI text the fallback may add next to payload strings.
UI_STRINGS = frozenset(
    {
        # Site chrome (PublicLayout).
        "thestill",
        "Top podcasts",
        "Sign in",
        "Read your podcasts.",
        "Open source on GitHub",
        "Terms",
        "Privacy",
        "·",
        "Summaries and quotes are produced by a language model and speech recognition; they can be wrong.",
        # Episode page.
        "With",
        "and",
        ",",
        "others",
        "Summary",
        "Read the full summary",
        "Also:",
        "Quotes from this episode",
        "at",
        "From the publisher",
        "More from",
        "Apple Podcasts",
        "Spotify",
        "YouTube",
        "People in this episode",
        "Companies mentioned",
        "Products",
        "Topics",
        # Podcast page.
        "Podcast",
        "Explicit",
        "summarised episodes",
        "latest",
        "Listen on",
        "Hosted by",
        "Latest episodes",
        "Website",
        # Entity pages.
        "Person",
        "Company",
        "Product",
        "Topic",
        "Host of",
        "Heard on",
        "Discussed on",
        "show",
        "shows",
        "episode",
        "episodes",
        "since",
        "in the last 30 days",
        "Who's talking",
        "quote",
        "quotes",
        "What they said",
        "Appears on",
        "Host",
        "Recurring",
        "Guest on",
        "What others say about",
        "Latest on",
        "No quotes yet.",
        "Hosts often go unnamed in their own show.",
        "About",
        "(from Wikipedia)",
        "Read on Wikipedia ↗",
        "Facts",
        "(Wikidata)",
        "People",
        "Most discussed on",
        "Often discussed with: people",
        "Often discussed with: companies",
        "Often discussed with: products",
        "Often discussed with: topics",
        "ep",
        "more from this episode",
        # Landing and top pages.
        "Read your podcasts.",
        "Latest summary",
        "Read the whole summary →",
        "Who said what this week:",
        "Who said what this month:",
        "See all →",
        "Discussed by",
        "speakers on",
        "show.",
        "shows.",
        "What you get when you sign in",
        "Your inbox",
        "Every new episode of the shows you follow, summarised the morning it drops.",
        "Your briefing",
        "One page across all your shows each morning, narrated if you like.",
        "Your knowledge base",
        "Search every mention of anything across every show you follow, and ask Claude about it.",
        "thestill turns every episode you follow into a summary you can read in three minutes, with every quote "
        "attributed and timed, and a morning briefing across all of them.",
        "Recently summarised",
        "Updated daily",
        "Shows with summaries open on thestill.",
        "Read summaries",
        "Apple Podcasts ↗",
    }
)

#: Derived values: counts ("12", "3×", "+2", "4 ep"), timestamps ("30:34", "1:02:07").
UI_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^\+?\d+(?:×| ep)?$"),
    re.compile(r"^\d+:\d{2}(?::\d{2})?$"),
)

_MENTIONED_TITLES = {
    "person": "People in this episode",
    "company": "Companies mentioned",
    "product": "Products",
    "topic": "Topics",
}
_RELATED_TITLES = {
    "person": "Often discussed with: people",
    "company": "Often discussed with: companies",
    "product": "Often discussed with: products",
    "topic": "Often discussed with: topics",
}
_TYPE_LABEL = {"person": "Person", "company": "Company", "product": "Product", "topic": "Topic"}
_TYPE_ORDER = ("person", "company", "product", "topic")
_ABOUT_WORDS = 60
_PER_EPISODE_GROUP = 2
_PLATFORM_LABEL = {"apple": "Apple Podcasts", "spotify": "Spotify", "youtube": "YouTube"}


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def text(value: Any) -> str:
    """An escaped text node."""
    return html.escape("" if value is None else str(value), quote=False)


def el(tag: str, *children: str, **attrs: Any) -> str:
    """``<tag attrs>children</tag>``; ``children`` are already-safe HTML.
    Attribute names use ``_`` for ``-`` and a trailing ``_`` for keywords
    (``class_``); ``None`` and ``False`` drop the attribute."""
    parts = []
    for name, value in attrs.items():
        if value is None or value is False:
            continue
        name = name.rstrip("_").replace("_", "-")
        parts.append(name if value is True else f'{name}="{html.escape(str(value), quote=True)}"')
    opening = f"<{tag}{' ' + ' '.join(parts) if parts else ''}>"
    return f"{opening}{''.join(children)}</{tag}>"


def _join(items: Iterable[str]) -> str:
    return "".join(items)


def _safe_href(href: Optional[str]) -> Optional[str]:
    """``http``/``https`` URLs and site-relative paths; anything else
    (``javascript:``, ``data:``, ``//host``, ``?cite=``) is None."""
    href = (href or "").strip()
    if re.match(r"^https?://", href, re.I):
        return href
    if href.startswith("/") and not href.startswith("//"):
        return href
    return None


def _external(href: Optional[str], label: str, **attrs: Any) -> str:
    safe = _safe_href(href)
    if safe is None or safe.startswith("/"):
        return text(label)
    return el("a", text(label), href=safe, rel="noopener", **attrs)


def _date(iso: Optional[str]) -> str:
    """``<time datetime="…">14 Oct 2026</time>``, or nothing."""
    if not iso:
        return ""
    try:
        value = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return el("time", text(f"{value.day} {value:%b %Y}"), datetime=str(iso))


def _timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def clip_words(value: Optional[str], words: int = _DESCRIPTION_WORDS) -> str:
    """The first ``words`` words of ``value``, with ``…`` when cut."""
    parts = (value or "").split()
    return " ".join(parts) if len(parts) <= words else " ".join(parts[:words]) + "…"


def sign_in_path(next_path: str, intent: Optional[str] = None) -> str:
    """``/login#next=…&intent=…`` as ``signInPath`` in ``utils/signIn.ts``
    builds it. The fragment survives a new tab like any href but is never
    sent to the server and makes no distinct URL for a crawler, so every
    page shares one robots-blocked ``/login`` instead of one
    ``/login?next=…`` each (5,851 of them in Search Console, 2026-10-26)."""
    params = {"next": next_path}
    if intent:
        params["intent"] = intent
    return f"/login#{urlencode(params)}"


def entity_path(ref: Dict[str, Any]) -> str:
    return f"/entities/{ref['type']}/{ref['slug']}"


def episode_path(podcast_slug: str, episode_slug: str) -> str:
    return f"/podcasts/{podcast_slug}/episodes/{episode_slug}"


# ---------------------------------------------------------------------------
# Markdown (summary leads)
# ---------------------------------------------------------------------------


def _markdown() -> MarkdownIt:
    """CommonMark with GFM tables and strikethrough (as ``remark-gfm`` in
    ``SummaryViewer``) and raw HTML off: summaries are unsanitised LLM
    output (spec #96, FM-7)."""
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
    # Parse every link; the renderer decides what stays a link (a dropped
    # one keeps its text, as react-markdown does for an unsafe URL).
    md.validateLink = lambda _url: True

    def link_open(renderer, tokens, idx, options, env):
        token = tokens[idx]
        href = _safe_href(token.attrGet("href"))
        env.setdefault("_links", []).append(href is not None)
        if href is None:
            return ""
        token.attrs = {"href": href}
        if not href.startswith("/"):
            token.attrSet("rel", "noopener")
        return renderer.renderToken(tokens, idx, options, env)

    def link_close(renderer, tokens, idx, options, env):
        if not env.get("_links", [True]).pop():
            return ""
        return renderer.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", link_open)
    md.add_render_rule("link_close", link_close)
    # The page renders images; the fallback leaves them out (no text).
    md.add_render_rule("image", lambda *_args: "")
    return md


_MD = _markdown()


def render_markdown(source: str) -> str:
    return _MD.render(source or "", {})


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


@dataclass
class Fallback:
    """One page's server-written content and its data block entries."""

    html: str
    queries: List[Dict[str, Any]]


def query(name: str, args: Sequence[Any], data: Dict[str, Any], page_param: Any = None) -> Dict[str, Any]:
    """A data-block entry. ``name`` is a ``publicKeys`` factory in
    ``api/public.ts`` (one definition of each React Query key); an entry
    with ``page_param`` seeds an infinite query's first page."""
    entry: Dict[str, Any] = {"query": name, "args": list(args), "data": data}
    if page_param is not None:
        entry["page_param"] = page_param
    return entry


def _chrome(page_path: str, main: str) -> str:
    """PublicLayout around ``main``: header, ``<main>``, footer."""
    header = el(
        "header",
        el(
            "div",
            el("a", text("thestill"), href="/", class_="ts-wordmark"),
            el(
                "nav",
                el("a", text("Top podcasts"), href="/top", class_="ts-header-link"),
                class_="ts-header-nav",
                aria_label="Main",
            ),
            el(
                "a",
                el("span", text("Sign in")),
                href=sign_in_path(page_path),
                rel="nofollow",
                class_="ts-btn ts-btn-primary",
            ),
            class_="ts-header-inner",
        ),
        class_="ts-header",
    )
    footer = el(
        "footer",
        el(
            "div",
            el(
                "div",
                el(
                    "div",
                    el("a", text("thestill"), href="/", class_="ts-wordmark"),
                    el("span", text("Read your podcasts."), class_="ts-footer-line"),
                    class_="ts-footer-brand",
                ),
                class_="ts-footer-top",
            ),
            el(
                "p",
                _external("https://github.com/ssarunic/thestill", "Open source on GitHub"),
                " ",
                text("·"),
                " ",
                el("a", text("Terms"), href="/terms"),
                " ",
                text("·"),
                " ",
                el("a", text("Privacy"), href="/privacy"),
                " ",
                text("·"),
                " ",
                el(
                    "span",
                    text(
                        "Summaries and quotes are produced by a language model and speech recognition; they can be wrong."
                    ),
                ),
                class_="ts-footer-legal",
            ),
            class_="ts-footer-inner",
        ),
        class_="ts-footer",
    )
    return el("div", header, el("main", main, class_="ts-main", id="main"), footer, class_="public-theme")


def _people_line(people: List[Dict[str, Any]], limit: int = _WITH_PEOPLE) -> str:
    """ "With A, B and C" (and "and N others" past ``limit``), as the page."""
    if not people:
        return ""
    shown = people[:limit]
    parts = [el("span", text("With")), " "]
    for i, person in enumerate(shown):
        if i > 0:
            parts.append(" " + text("and") + " " if i == len(shown) - 1 else text(",") + " ")
        parts.append(el("a", text(person["name"]), href=entity_path(person)))
    if len(people) > limit:
        parts += [" ", text("and"), " ", el("span", text(len(people) - limit)), " ", text("others")]
    return el("p", *parts, class_="ts-eprow-guests")


def _listen_on(links: List[Tuple[str, Optional[str]]]) -> str:
    seen = set()
    items = []
    for platform in ("apple", "spotify", "youtube"):
        for name, url in links:
            if name == platform and url and platform not in seen and _safe_href(url):
                seen.add(platform)
                items.append(_external(url, _PLATFORM_LABEL[platform], class_="ts-listen-link"))
    return el("div", el("div", *items, class_="ts-listen-row"), class_="ts-listen") if items else ""


def _quote(
    quote: Dict[str, Any],
    *,
    show_speaker: bool = True,
    show_show: bool = False,
    show_date: bool = False,
    featured: bool = False,
) -> str:
    """One quote as ``QuoteCard`` shows it: the text, then speaker, show and
    date as the page's variant does, and an ``#t=`` link (never ``?t=``)."""
    href = f"{episode_path(quote['podcast']['slug'], quote['episode']['slug'])}#t={int(quote['start_seconds'])}"
    meta: List[str] = []
    if show_speaker:
        meta.append(
            el(
                "a",
                text(quote["speaker"]["name"]),
                href=entity_path({"type": "person", "slug": quote["speaker"]["slug"]}),
            )
        )
    if show_show:
        meta.append(el("a", text(quote["podcast"]["title"]), href=f"/podcasts/{quote['podcast']['slug']}"))
    if show_date and quote["episode"].get("pub_date"):
        meta.append(_date(quote["episode"]["pub_date"]))
    meta.append(text("at") + " " + el("a", text(_timestamp(quote["start_seconds"])), href=href))
    body = text(quote["text"]) if featured else text(f"\u201c{quote['text']}\u201d")
    return el(
        "figure",
        el("blockquote", body, class_="ts-quote-text"),
        el("figcaption", *_separated(meta), class_="ts-quote-meta"),
        class_=f"ts-quote ts-quote-{'featured' if featured else 'stream'}",
    )


def _separated(parts: List[str]) -> List[str]:
    """``parts`` with `` · `` between them."""
    out: List[str] = []
    for i, part in enumerate(parts):
        if i:
            out.append(" " + text("·") + " ")
        out.append(part)
    return out


def _plural(count: int, one: str, many: str) -> str:
    """``<span>3</span> shows``: the count and the word as separate nodes."""
    return el("span", text(count)) + " " + text(one if count == 1 else many)


def _entity_groups(items: List[Dict[str, Any]], titles: Dict[str, str], count: Callable[[Dict[str, Any]], str]) -> str:
    """Titled lists per type, the first eight of each (what the page shows
    before "Show all")."""
    out = []
    for kind in _TYPE_ORDER:
        group = [item for item in items if item["type"] == kind][:_GROUP_VISIBLE]
        if not group:
            continue
        rows = [
            el(
                "li",
                el("a", text(item["name"]), href=entity_path(item)),
                " ",
                el("span", text(count(item)), class_="ts-related-count"),
            )
            for item in group
        ]
        out.append(
            el(
                "section",
                el("h3", text(titles[kind]), class_="ts-rail-label"),
                el("ul", *rows, class_="ts-related"),
                class_="ts-rail-module",
            )
        )
    return _join(out)


def _section(heading: str, body: str, level: str = "h2", **attrs: Any) -> str:
    cls = "ts-h2" if level == "h2" else "ts-rail-label"
    return el("section", el(level, text(heading), class_=cls), body, **attrs)


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------


def _try(build: Callable[[], Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A payload, or None when that module 404s (the page hides it too)."""
    try:
        return build()
    except HTTPException:
        return None


def episode_fallback(state: "AppState", podcast_slug: str, episode_slug: str, header_only: bool) -> Fallback:
    from .routes import api_public as api

    episode_payload = api.public_episode_payload(state, podcast_slug, episode_slug)
    episode = episode_payload["episode"]
    show = episode["podcast"]
    queries = [query("episode", [podcast_slug, episode_slug], episode_payload)]
    page_path = episode_path(podcast_slug, episode_slug)

    eyebrow = [el("a", text(show["title"]), href=f"/podcasts/{show['slug']}")]
    if episode["pub_date"]:
        eyebrow += [" ", text("·"), " ", _date(episode["pub_date"])]
    if episode["duration_formatted"]:
        eyebrow += [" ", text("·"), " ", el("span", text(episode["duration_formatted"]))]
    people = [*episode["hosts"], *episode["guests"]]
    platform = {link["platform"]: link["url"] for link in episode["platform_links"]}
    hero = el(
        "section",
        el(
            "div",
            el("p", *eyebrow, class_="ts-eyebrow"),
            el("h1", text(episode["title"]), class_="ts-episode-title"),
            _people_line(people),
            (
                ""
                if header_only
                else _listen_on(
                    [
                        ("apple", platform.get("apple") or show["apple_url"]),
                        ("spotify", platform.get("spotify") or show["spotify_url"]),
                        ("youtube", platform.get("youtube") or show["youtube_url"]),
                    ]
                )
            ),
            class_="ts-hero-body",
        ),
        class_="ts-hero",
    )
    if header_only:
        return Fallback(_chrome(page_path, el("article", hero, class_="ts-container")), queries)

    main: List[str] = []
    summary = _try(lambda: api.public_episode_summary_payload(state, podcast_slug, episode_slug))
    if summary is not None:
        queries.append(query("summary", [podcast_slug, episode_slug], summary))
        more = ""
        if summary["more_sections"]:
            more = el(
                "p",
                el("a", text("Read the full summary"), href=sign_in_path(page_path, "summary"), rel="nofollow"),
                " ",
                text("Also:"),
                " ",
                el("span", text(" · ".join(summary["more_sections"]))),
                class_="ts-summary-more",
            )
        main.append(
            el(
                "section",
                el(
                    "div",
                    el("div", el("span", text("Summary"), class_="ts-tab ts-tab-active"), class_="ts-tabs-list"),
                    class_="ts-tabs",
                ),
                el("div", render_markdown(summary["content"]), class_="ts-summary"),
                more,
            )
        )

    quotes = _try(lambda: api.public_episode_quotes_payload(state, podcast_slug, episode_slug))
    rail: List[str] = []
    if quotes is not None:
        queries.append(query("episodeQuotes", [podcast_slug, episode_slug, None, None], quotes, page_param=1))
        if sum(s["quotes"] for s in quotes["speakers"]) >= 2 and quotes["quotes"]:
            main.append(
                _section(
                    "Quotes from this episode",
                    el("div", *(_quote(q) for q in quotes["quotes"]), class_="ts-quote-list"),
                    class_="ts-section",
                )
            )
        rail.append(_entity_groups(quotes["mentioned"], _MENTIONED_TITLES, lambda item: f"{item['count']}×"))

    if episode["description"]:
        rail.append(
            _section(
                "From the publisher",
                el("p", text(clip_words(episode["description"])), class_="ts-about ts-description ts-muted"),
                level="h3",
                class_="ts-rail-module",
            )
        )

    listing = _try(lambda: api.public_podcast_episodes_payload(state, podcast_slug))
    if listing is not None:
        queries.append(query("podcastEpisodes", [podcast_slug], listing, page_param=0))
        others = [e for e in listing["episodes"] if e["slug"] != episode_slug][:_MORE_FROM]
        if others:
            rows = [
                el(
                    "li",
                    el(
                        "a",
                        el("span", _date(e["pub_date"]), class_="ts-meta"),
                        " ",
                        el("span", text(e["title"])),
                        href=episode_path(podcast_slug, e["slug"]),
                    ),
                )
                for e in others
            ]
            rail.append(
                el(
                    "section",
                    el("h3", text("More from"), " ", el("span", text(show["title"])), class_="ts-rail-label"),
                    el("ul", *rows, class_="ts-related"),
                    class_="ts-rail-module",
                )
            )

    body = el(
        "div",
        el("div", *main, class_="ts-stack"),
        el("aside", *rail, class_="ts-rail"),
        class_="ts-columns",
    )
    return Fallback(_chrome(page_path, el("article", hero, body, class_="ts-container")), queries)


def _episode_row(item: Dict[str, Any], podcast_slug: str, podcast_title: Optional[str] = None) -> str:
    """``EpisodeRow``: date, duration, title, gist and guests."""
    meta: List[str] = []
    if podcast_title:
        meta.append(el("a", text(podcast_title), href=f"/podcasts/{podcast_slug}"))
    if item.get("pub_date"):
        meta.append(_date(item["pub_date"]))
    if item.get("duration_formatted"):
        meta.append(el("span", text(item["duration_formatted"])))
    return el(
        "article",
        el("p", *_separated(meta), class_="ts-meta"),
        el("h3", el("a", text(item["title"]), href=episode_path(podcast_slug, item["slug"])), class_="ts-eprow-title"),
        el("p", text(item["summary_preview"]), class_="ts-eprow-desc") if item.get("summary_preview") else "",
        _people_line(item.get("guests") or [], limit=len(item.get("guests") or [])),
        class_="ts-eprow",
    )


def podcast_fallback(state: "AppState", podcast_slug: str, header_only: bool) -> Fallback:
    from .routes import api_public as api

    podcast_payload = api.public_podcast_payload(state, podcast_slug)
    show = podcast_payload["podcast"]
    queries = [query("podcast", [podcast_slug], podcast_payload)]
    page_path = f"/podcasts/{podcast_slug}"

    eyebrow = [text("Podcast")]
    if show["author"]:
        eyebrow += [" " + text("·") + " ", el("span", text(show["author"]))]
    if show["explicit"]:
        eyebrow += [" " + text("·") + " ", text("Explicit")]
    hero_parts = [el("p", *eyebrow, class_="ts-eyebrow"), el("h1", text(show["title"]), class_="ts-page-title")]
    if header_only:
        hero = el("section", el("div", *hero_parts, class_="ts-hero-body"), class_="ts-hero")
        return Fallback(_chrome(page_path, el("article", hero, class_="ts-container")), queries)

    if show["description"]:
        hero_parts.append(el("p", text(clip_words(show["description"])), class_="ts-standfirst ts-description"))
    meta = [_plural(show["summarised_count"], "summarised episodes", "summarised episodes")]
    if show["latest_pub_date"]:
        meta.append(text("latest") + " " + _date(show["latest_pub_date"]))
    hero_parts.append(el("p", *_separated(meta), class_="ts-meta"))
    hero = el("section", el("div", *hero_parts, class_="ts-hero-body"), class_="ts-hero")

    main: List[str] = []
    listen = _listen_on(
        [("apple", show["apple_url"]), ("spotify", show["spotify_url"]), ("youtube", show["youtube_url"])]
    )
    if listen:
        main.append(el("section", el("span", text("Listen on"), class_="ts-rail-label"), listen))
    people = [*show["hosts"], *show["recurring"]][:_WITH_PEOPLE]
    if people:
        links = [el("li", el("a", text(p["name"]), href=entity_path(p))) for p in people]
        main.append(
            el("section", el("span", text("Hosted by"), class_="ts-rail-label"), el("ul", *links), class_="ts-section")
        )
    listing = _try(lambda: api.public_podcast_episodes_payload(state, podcast_slug))
    if listing is not None:
        queries.append(query("podcastEpisodes", [podcast_slug], listing, page_param=0))
        if listing["episodes"]:
            rows = [_episode_row(item, podcast_slug) for item in listing["episodes"]]
            main.append(_section("Latest episodes", el("div", *rows), class_="ts-section"))

    rail: List[str] = []
    website = _safe_href(show["website_url"])
    if website and not website.startswith("/"):
        label = re.sub(r"/$", "", re.sub(r"^https?://", "", website, flags=re.I))
        rail.append(
            _section("Website", _external(website, label, class_="ts-link"), level="h3", class_="ts-rail-module")
        )

    body = el("div", el("div", *main, class_="ts-stack"), el("aside", *rail, class_="ts-rail"), class_="ts-columns")
    return Fallback(_chrome(page_path, el("article", hero, body, class_="ts-container")), queries)


def _quote_stream(quotes: List[Dict[str, Any]], show_speaker: bool) -> str:
    """``QuoteStream``: quotes grouped by episode, two per group shown (the
    rest sit behind "+N more from this episode")."""
    groups: List[List[Dict[str, Any]]] = []
    for quote in quotes:
        key = (quote["podcast"]["slug"], quote["episode"]["slug"])
        if groups and (groups[-1][0]["podcast"]["slug"], groups[-1][0]["episode"]["slug"]) == key:
            groups[-1].append(quote)
        else:
            groups.append([quote])
    out = []
    for group in groups:
        first = group[0]
        head_meta = [el("span", text(first["podcast"]["title"]))]
        if first["episode"].get("pub_date"):
            head_meta.append(_date(first["episode"]["pub_date"]))
        head = el(
            "a",
            el("span", *_separated(head_meta), class_="ts-meta"),
            " ",
            el("span", text(first["episode"]["title"]), class_="ts-ep-head-title"),
            href=episode_path(first["podcast"]["slug"], first["episode"]["slug"]),
            class_="ts-ep-head",
        )
        cards = [_quote(q, show_speaker=show_speaker) for q in group[:_PER_EPISODE_GROUP]]
        more = ""
        if len(group) > _PER_EPISODE_GROUP:
            more = el(
                "p",
                el("span", text(f"+{len(group) - _PER_EPISODE_GROUP}")),
                " ",
                text("more from this episode"),
                class_="ts-meta",
            )
        out.append(el("div", head, *cards, more, class_="ts-ep-group"))
    return el("div", *out, class_="ts-quote-list")


def entity_fallback(state: "AppState", entity_type: str, slug: str, header_only: bool) -> Fallback:
    from .routes import api_public as api

    payload = api.public_entity_payload(state, entity_type, slug)
    queries = [query("entity", [entity_type, slug], payload)]
    page_path = f"/entities/{entity_type}/{slug}"
    entity = payload["entity"]
    name = entity["name"]
    is_person = entity_type == "person"
    enrichment = payload.get("enrichment") or {}
    headline = enrichment.get("headline") or entity.get("description")
    host_of = payload["roles"]["hosts"][0] if payload["roles"]["hosts"] else None

    eyebrow = [text(_TYPE_LABEL.get(entity["type"], "Topic"))]
    if host_of:
        eyebrow += [
            " " + text("·") + " ",
            el("a", text("Host of") + " " + el("span", text(host_of["title"])), href=f"/podcasts/{host_of['slug']}"),
        ]
    hero_parts = [el("p", *eyebrow, class_="ts-eyebrow"), el("h1", text(name), class_="ts-page-title")]
    if header_only:
        hero = el("section", el("div", *hero_parts, class_="ts-hero-body"), class_="ts-hero")
        return Fallback(_chrome(page_path, el("article", hero, class_="ts-container")), queries)

    if headline:
        hero_parts.append(el("p", text(headline), class_="ts-standfirst"))
    stats = payload["stats"]
    if stats["episodes"] > 0:
        meta = [
            text("Heard on" if is_person else "Discussed on") + " " + _plural(stats["shows"], "show", "shows"),
            _plural(stats["episodes"], "episode", "episodes"),
        ]
        if not is_person and stats["last_30_days"] > 0:
            meta.append(el("span", text(stats["last_30_days"])) + " " + text("in the last 30 days"))
        if is_person and stats["since"]:
            meta.append(text("since") + " " + _date(stats["since"]))
        hero_parts.append(el("p", *_separated(meta), class_="ts-meta"))
    hero = el("section", el("div", *hero_parts, class_="ts-hero-body"), class_="ts-hero")

    parts: List[str] = [hero]
    featured = payload.get("featured_quote")
    if featured:
        parts.append(
            el(
                "div",
                _quote(featured, show_speaker=not is_person, show_show=True, show_date=True, featured=True),
                class_="ts-featured",
            )
        )

    main: List[str] = []
    counts = payload["quote_counts"]
    if not is_person and payload["speakers"]:
        rows = []
        for person in payload["speakers"]:
            rows.append(
                el(
                    "li",
                    el(
                        "a", text(person["name"]), href=f"/entities/person/{person['slug']}", class_="ts-personrow-name"
                    ),
                    (
                        el("p", text(f"\u201c{person['quote']}\u201d"), class_="ts-personrow-quote")
                        if person["quote"]
                        else ""
                    ),
                    el(
                        "p",
                        *_separated(
                            [_plural(person["quotes"], "quote", "quotes"), _plural(person["shows"], "show", "shows")]
                        ),
                        class_="ts-personrow-counts",
                    ),
                    class_="ts-personrow",
                )
            )
        main.append(_section("Who's talking", el("ul", *rows), class_="ts-section"))
    if is_person and counts["said"] > 0:
        said = _try(lambda: api.public_entity_quotes_payload(state, entity_type, slug, "said"))
        if said is not None:
            queries.append(query("entityQuotes", [entity_type, slug, "said", None], said, page_param=1))
            main.append(
                _section("What they said", _quote_stream(said["quotes"], show_speaker=False), class_="ts-section")
            )
    roles = payload["roles"]
    if is_person and (roles["hosts"] or roles["recurring"] or roles["guest_episodes"]):
        tiles = [
            el(
                "li",
                el("a", text(r["title"]), href=f"/podcasts/{r['slug']}", class_="ts-showtile-title"),
                " ",
                el(
                    "span",
                    text(role),
                    *(
                        [" " + text("·") + " ", _plural(r["count"], "episode", "episodes")]
                        if r["count"] is not None
                        else []
                    ),
                    class_="ts-showtile-meta",
                ),
                class_="ts-showtile",
            )
            for role, items in (("Host", roles["hosts"]), ("Recurring", roles["recurring"]))
            for r in items
        ]
        section = [el("ul", *tiles, class_="ts-tiles")] if tiles else []
        if roles["guest_episodes"]:
            guest_rows = []
            for g in roles["guest_episodes"]:
                meta = [el("span", text(g["podcast_title"]))]
                if g.get("published_at"):
                    meta.append(_date(g["published_at"]))
                guest_rows.append(
                    el(
                        "li",
                        el(
                            "a",
                            el("span", *_separated(meta), class_="ts-meta"),
                            " ",
                            el("span", text(g["episode_title"])),
                            href=episode_path(g["podcast_slug"], g["episode_slug"]),
                        ),
                    )
                )
            section += [el("h3", text("Guest on"), class_="ts-rail-label"), el("ul", *guest_rows, class_="ts-related")]
        main.append(_section("Appears on", _join(section), class_="ts-section"))
    if counts["about"] > 0:
        about = _try(lambda: api.public_entity_quotes_payload(state, entity_type, slug, "about"))
        if about is not None:
            queries.append(query("entityQuotes", [entity_type, slug, "about", None], about, page_param=1))
            heading = (
                text("What others say about") + " " + el("span", text(name))
                if is_person
                else text("Latest on") + " " + el("span", text(name))
            )
            main.append(
                el(
                    "section",
                    el("h2", heading, class_="ts-h2"),
                    _quote_stream(about["quotes"], show_speaker=True),
                    class_="ts-section",
                )
            )
    if counts["said"] + counts["about"] == 0:
        note = [text("No quotes yet.")]
        if roles["hosts"]:
            note += [" ", text("Hosts often go unnamed in their own show.")]
        main.append(el("p", *note, class_="ts-muted"))

    rail: List[str] = []
    if enrichment.get("wikipedia_extract"):
        extract = [el("p", text(clip_words(enrichment["wikipedia_extract"], _ABOUT_WORDS)), class_="ts-about")]
        if enrichment.get("wikipedia_url"):
            extract.append(_external(enrichment["wikipedia_url"], "Read on Wikipedia ↗", class_="ts-link"))
        rail.append(
            el(
                "section",
                el(
                    "h3",
                    text("About"),
                    " ",
                    el("span", text("(from Wikipedia)"), class_="ts-rail-source"),
                    class_="ts-rail-label",
                ),
                *extract,
                class_="ts-rail-module",
            )
        )
    if enrichment.get("facts"):
        rows = [
            el(
                "div",
                el("dt", text(f["label"])),
                el("dd", _external(f["url"], f["value"]) if f.get("url") else text(f["value"])),
            )
            for f in enrichment["facts"]
        ]
        rail.append(
            el(
                "section",
                el(
                    "h3",
                    text("Facts"),
                    " ",
                    el("span", text("(Wikidata)"), class_="ts-rail-source"),
                    class_="ts-rail-label",
                ),
                el("dl", *rows, class_="ts-facts-list"),
                class_="ts-rail-module",
            )
        )
    if not is_person:
        people: List[Dict[str, Any]] = []
        for aff in enrichment.get("affiliations") or []:
            ref = aff.get("ref")
            if not ref or ref.get("type") != "person":
                continue
            existing = next((p for p in people if p["slug"] == ref["slug"]), None)
            if existing is None:
                people.append({"slug": ref["slug"], "label": aff["label"], "relations": [aff["relation"]]})
            elif aff["relation"] not in existing["relations"]:
                existing["relations"].append(aff["relation"])
        if people:
            rows = [
                el(
                    "li",
                    el("a", text(p["label"]), href=f"/entities/person/{p['slug']}"),
                    " ",
                    el("span", *_separated([text(r) for r in p["relations"]]), class_="ts-related-count"),
                )
                for p in people
            ]
            rail.append(_section("People", el("ul", *rows, class_="ts-related"), level="h3", class_="ts-rail-module"))
        if payload["most_discussed_on"]:
            rows = [
                el(
                    "li",
                    el("a", text(show["title"]), href=f"/podcasts/{show['slug']}"),
                    *(
                        [" ", el("span", text(show["count"]), class_="ts-related-count")]
                        if show["count"] is not None
                        else []
                    ),
                )
                for show in payload["most_discussed_on"]
            ]
            rail.append(
                _section("Most discussed on", el("ul", *rows, class_="ts-related"), level="h3", class_="ts-rail-module")
            )
    if len(payload["related"]) >= 3:
        rail.append(_entity_groups(payload["related"], _RELATED_TITLES, lambda item: f"{item['episode_count']} ep"))

    parts.append(
        el("div", el("div", *main, class_="ts-stack"), el("aside", *rail, class_="ts-rail"), class_="ts-columns")
    )
    return Fallback(_chrome(page_path, el("article", *parts, class_="ts-container")), queries)


_OUTCOMES = (
    ("Your inbox", "Every new episode of the shows you follow, summarised the morning it drops."),
    ("Your briefing", "One page across all your shows each morning, narrated if you like."),
    (
        "Your knowledge base",
        "Search every mention of anything across every show you follow, and ask Claude about it.",
    ),
)
_LANDING_STANDFIRST = (
    "thestill turns every episode you follow into a summary you can read in three minutes, with every quote "
    "attributed and timed, and a morning briefing across all of them."
)


def landing_fallback(state: "AppState", header_only: bool) -> Fallback:
    from .routes import api_public as api

    hero = el(
        "section",
        el("h1", text("Read your podcasts."), class_="ts-page-title"),
        el("p", text(_LANDING_STANDFIRST), class_="ts-standfirst"),
        class_="ts-hero",
    )
    queries: List[Dict[str, Any]] = []
    if header_only:
        return Fallback(_chrome("/", el("article", hero, class_="ts-container")), queries)

    main: List[str] = []
    latest = api.public_recent_summaries_payload(state, 1)
    queries.append(query("recent", [1], latest))
    if latest["episodes"] and latest["episodes"][0].get("podcast"):
        episode = latest["episodes"][0]
        show = episode["podcast"]
        href = episode_path(show["slug"], episode["slug"])
        summary = _try(lambda: api.public_episode_summary_payload(state, show["slug"], episode["slug"]))
        if summary is not None:
            queries.append(query("summary", [show["slug"], episode["slug"]], summary))
            lead = el("div", render_markdown(summary["content"]), class_="ts-summary")
        else:
            lead = el("p", text(episode["summary_preview"]), class_="ts-standfirst")
        main.append(
            el(
                "section",
                el("h2", text("Latest summary"), class_="ts-h2"),
                el("p", _date(episode["pub_date"]), class_="ts-meta"),
                el("p", el("a", text(show["title"]), href=f"/podcasts/{show['slug']}"), class_="ts-link"),
                el("h3", el("a", text(episode["title"]), href=href), class_="ts-eprow-title"),
                lead,
                el("p", el("a", text("Read the whole summary →"), href=href, class_="ts-link")),
                class_="ts-section",
            )
        )

    trending = api.public_who_said_what_payload(state)
    queries.append(query("whoSaidWhat", [], trending))
    if trending["entity"] and trending["quotes"]:
        subject = trending["entity"]
        when = "this week" if trending["days"] == 7 else "this month"
        main.append(
            el(
                "section",
                el(
                    "h2",
                    text(f"Who said what {when}:") + " " + el("span", text(subject["name"])),
                    class_="ts-h2",
                ),
                el("p", el("a", text("See all →"), href=entity_path(subject), class_="ts-link")),
                el(
                    "p",
                    text("Discussed by")
                    + " "
                    + el("span", text(trending["speakers"]))
                    + " "
                    + text("speakers on")
                    + " "
                    + _plural(trending["shows"], "show.", "shows."),
                    class_="ts-muted",
                ),
                el(
                    "div",
                    *(_quote(q, show_show=True, show_date=True) for q in trending["quotes"]),
                    class_="ts-quote-list",
                ),
                class_="ts-section",
            )
        )

    outcomes = [el("div", el("dt", text(title)), el("dd", text(body))) for title, body in _OUTCOMES]
    main.append(_section("What you get when you sign in", el("dl", *outcomes), class_="ts-section"))

    fresh = api.public_recent_summaries_payload(state, 6)
    queries.append(query("recent", [6], fresh))
    rows = [
        _episode_row(item, item["podcast"]["slug"], item["podcast"]["title"])
        for item in fresh["episodes"]
        if item.get("podcast")
    ]
    if rows:
        main.append(_section("Recently summarised", el("div", *rows), class_="ts-section"))

    return Fallback(
        _chrome("/", el("article", hero, el("div", *main, class_="ts-stack"), class_="ts-container")), queries
    )


def top_fallback(state: "AppState", header_only: bool, country: Optional[str]) -> Fallback:
    from .routes import api_public as api

    hero = el(
        "section",
        el("p", text("Updated daily"), class_="ts-eyebrow"),
        el("h1", text("Top podcasts"), class_="ts-page-title"),
        # The page names the chart's country in the browser's locale; the
        # server cannot, so it keeps only the sentence that does not.
        el("p", text("Shows with summaries open on thestill."), class_="ts-standfirst"),
        class_="ts-hero",
    )
    if header_only:
        return Fallback(_chrome("/top", el("article", hero, class_="ts-container")), [])
    chart = api.public_top_chart_payload(state, country=country)
    rows = []
    for row in chart["top_podcasts"]:
        byline = [text(v) for v in (row.get("artist"), row.get("category")) if v]
        inner = [
            el("span", text(row["rank"]), class_="ts-meta"),
            " ",
            el("span", text(row["name"]), class_="ts-ep-head-title"),
            " ",
            el("span", *_separated(byline), class_="ts-meta"),
        ]
        if row.get("podcast_slug"):
            inner += [" ", el("span", text("Read summaries"), class_="ts-link")]
            item = el("a", *inner, href=f"/podcasts/{row['podcast_slug']}", class_="ts-ep-head")
        elif _safe_href(row.get("apple_url")):
            inner += [" ", el("span", text("Apple Podcasts ↗"), class_="ts-link")]
            item = el("a", *inner, href=_safe_href(row["apple_url"]), rel="noopener", class_="ts-ep-head")
        else:
            item = el("div", *inner, class_="ts-ep-head")
        rows.append(el("li", item, class_="ts-eprow"))
    body = el("ol", *rows) if rows else ""
    return Fallback(
        _chrome("/top", el("article", hero, body, class_="ts-container")),
        [query("top", [None, None], chart)],
    )


def build_fallback(
    state: "AppState", path: str, header_only: bool, request: Optional["Request"] = None
) -> Optional[Fallback]:
    """The fallback for a public content path, or None for a kind that has
    none. Raises ``HTTPException`` (404) when the page is not public."""
    kind = shell_kind(path)
    if kind not in PRERENDER_KINDS:
        return None
    parts = path.strip("/").split("/")
    if kind == "landing":
        return landing_fallback(state, header_only)
    if kind == "top":
        from .routes.api_public import shell_visitor_country

        # Same region rule as the API: the chart this visitor's page shows.
        country = shell_visitor_country(request) if request is not None and not header_only else None
        return top_fallback(state, header_only, country)
    if kind == "episode":
        return episode_fallback(state, parts[1], parts[3], header_only)
    if kind == "podcast":
        return podcast_fallback(state, parts[1], header_only)
    if kind == "entity":
        return entity_fallback(state, parts[1], parts[2], header_only)
    return None


def render_body(kind: str, fallback: Fallback) -> str:
    """The marker's replacement: the fallback, then the data block."""
    bootstrap = {"v": BOOTSTRAP_VERSION, "page": kind, "viewer": "anonymous", "queries": fallback.queries}
    return (
        el("div", fallback.html, data_prerendered=kind)
        + f'<script type="application/json" id="{BOOTSTRAP_ID}">{safe_json(bootstrap)}</script>'
    )


def inject_body(template: str, body: Optional[str]) -> str:
    """Replace the body marker with ``body`` (left empty without one)."""
    if not body:
        return template
    return BODY_MARKER_RE.sub(lambda _m: body, template, count=1)


# ---------------------------------------------------------------------------
# Shell integration: switch, cap, shedding
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    body: Optional[str]
    #: ``true``, ``false`` or ``shed`` (the ``public_shell.served`` field).
    prerendered: str
    #: ``hit`` when every payload came from cache, ``miss`` otherwise.
    cache: Optional[str] = None


class Prerenderer:
    """Builds fallbacks under the ``public_prerender_enabled`` switch and a
    process-wide cap on uncached builds (spec #105 §4). A request over the
    cap waits up to ``prerender_slot_wait_seconds`` for a slot; only when
    none frees up in time does it get the head-only shell. Shedding is on
    load, never on who is asking.

    The wait is what keeps a crawler's ordinary concurrency from showing up
    as 5xx: Googlebot fetching a handful of cold entity pages at once used
    to see 503s the moment the cap was full, and Search Console filed them
    as server errors."""

    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._slots = threading.BoundedSemaphore(max(1, int(state.config.prerender_max_concurrent)))
        self._slot_wait = max(0.0, float(getattr(state.config, "prerender_slot_wait_seconds", 0.0) or 0.0))

    def enabled(self) -> bool:
        settings = getattr(getattr(self._state, "abuse", None), "settings", None)
        if settings is None:
            return bool(self._state.config.public_prerender_enabled)
        try:
            return bool(settings.get("public_prerender_enabled"))
        except Exception:  # noqa: BLE001 - an unreadable switch fails to today's shell
            logger.warning("public_prerender.switch_unreadable", exc_info=True)
            return False

    @staticmethod
    def log_shed(kind: str) -> None:
        logger.info("public_prerender.shed", kind=kind)

    @contextmanager
    def slot(self) -> Iterator[bool]:
        """One of the uncached-build slots, waited for up to the configured
        wait: yields False when none frees up in time (the caller sheds)."""
        acquired = self._slots.acquire(blocking=self._slot_wait > 0, timeout=self._slot_wait or None)
        try:
            yield acquired
        finally:
            if acquired:
                self._slots.release()

    def render(self, path: str, query_string: str, request: Optional["Request"] = None) -> Outcome:
        kind = shell_kind(path)
        if kind not in PRERENDER_KINDS or not self.enabled():
            return Outcome(None, "false")
        header_only = bool(query_string)
        try:
            try:
                with cache_only():
                    fallback = build_fallback(self._state, path, header_only, request)
                cache = "hit"
            except CacheMiss:
                with self.slot() as acquired:
                    if not acquired:
                        self.log_shed(kind)
                        return Outcome(None, "shed", "miss")
                    fallback = build_fallback(self._state, path, header_only, request)
                cache = "miss"
        except HTTPException:
            return Outcome(None, "false")  # gone since the head lookup
        except Exception as exc:  # noqa: BLE001 - the head-only shell still works
            logger.warning("public_prerender.failed", kind=kind, error=str(exc), exc_info=True)
            return Outcome(None, "false")
        if fallback is None:
            return Outcome(None, "false")
        return Outcome(render_body(kind, fallback), "true", cache)


_PRERENDERER_ATTR = "_prerenderer"


def get_prerenderer(state: "AppState") -> Prerenderer:
    prerenderer = getattr(state, _PRERENDERER_ATTR, None)
    if prerenderer is None:
        prerenderer = Prerenderer(state)
        setattr(state, _PRERENDERER_ATTR, prerenderer)
    return prerenderer
