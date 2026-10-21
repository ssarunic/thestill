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
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException
from markdown_it import MarkdownIt
from structlog import get_logger

from .public_access import CacheMiss, cache_only
from .public_shell import safe_json, shell_kind

if TYPE_CHECKING:
    from .dependencies import AppState

logger = get_logger(__name__)

BODY_MARKER_RE = re.compile(r"<!--thestill:body-->.*?<!--/thestill:body-->", re.S)
BOOTSTRAP_ID = "thestill-bootstrap"
#: Format version of the data block; the app ignores any other.
BOOTSTRAP_VERSION = 1

#: Page kinds that get server-written content.
PRERENDER_KINDS = frozenset({"episode"})

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
    }
)

#: Derived values: counts ("12", "3×"), timestamps ("30:34", "1:02:07").
UI_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^\d+×?$"),
    re.compile(r"^\d+:\d{2}(?::\d{2})?$"),
)

_MENTIONED_TITLES = {
    "person": "People in this episode",
    "company": "Companies mentioned",
    "product": "Products",
    "topic": "Topics",
}
_TYPE_ORDER = ("person", "company", "product", "topic")
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
    """``/login?next=…`` as ``signInPath`` in ``utils/signIn.ts`` builds it."""
    params = {"next": next_path}
    if intent:
        params["intent"] = intent
    return f"/login?{urlencode(params)}"


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
            el("a", el("span", text("Sign in")), href=sign_in_path(page_path), class_="ts-btn ts-btn-primary"),
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


def _quote(quote: Dict[str, Any]) -> str:
    """One quote as the page shows it in an episode stream: the text, the
    speaker and an ``#t=`` link (never ``?t=``)."""
    href = f"{episode_path(quote['podcast']['slug'], quote['episode']['slug'])}#t={int(quote['start_seconds'])}"
    return el(
        "figure",
        el("blockquote", text(f"“{quote['text']}”"), class_="ts-quote-text"),
        el(
            "figcaption",
            el(
                "a",
                text(quote["speaker"]["name"]),
                href=entity_path({"type": "person", "slug": quote["speaker"]["slug"]}),
            ),
            " ",
            text("·"),
            " ",
            text("at"),
            " ",
            el("a", text(_timestamp(quote["start_seconds"])), href=href),
            class_="ts-quote-meta",
        ),
        class_="ts-quote ts-quote-stream",
    )


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
                el("a", text("Read the full summary"), href=sign_in_path(page_path, "summary")),
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


def build_fallback(state: "AppState", path: str, header_only: bool) -> Optional[Fallback]:
    """The fallback for a public content path, or None for a kind that has
    none. Raises ``HTTPException`` (404) when the page is not public."""
    kind = shell_kind(path)
    if kind not in PRERENDER_KINDS:
        return None
    parts = path.strip("/").split("/")
    if kind == "episode":
        return episode_fallback(state, parts[1], parts[3], header_only)
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
    process-wide cap on uncached builds (spec #105 §4). Over the cap, the
    request gets the head-only shell: shedding is on load, never on who is
    asking."""

    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._slots = threading.BoundedSemaphore(max(1, int(state.config.prerender_max_concurrent)))

    def enabled(self) -> bool:
        settings = getattr(getattr(self._state, "abuse", None), "settings", None)
        if settings is None:
            return bool(self._state.config.public_prerender_enabled)
        try:
            return bool(settings.get("public_prerender_enabled"))
        except Exception:  # noqa: BLE001 - an unreadable switch fails to today's shell
            logger.warning("public_prerender.switch_unreadable", exc_info=True)
            return False

    def render(self, path: str, query_string: str) -> Outcome:
        kind = shell_kind(path)
        if kind not in PRERENDER_KINDS or not self.enabled():
            return Outcome(None, "false")
        header_only = bool(query_string)
        try:
            try:
                with cache_only():
                    fallback = build_fallback(self._state, path, header_only)
                cache = "hit"
            except CacheMiss:
                if not self._slots.acquire(blocking=False):
                    logger.info("public_prerender.shed", kind=kind)
                    return Outcome(None, "shed", "miss")
                try:
                    fallback = build_fallback(self._state, path, header_only)
                finally:
                    self._slots.release()
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
