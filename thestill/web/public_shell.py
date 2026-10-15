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

"""Per-page ``<head>`` for the SPA shell, robots.txt and sitemap.xml (spec #94 §3-4).

The body of every page is still rendered in the browser; crawlers and link
unfurlers get real titles, descriptions, canonical URLs, Open Graph tags and
JSON-LD from here. ``index.html`` carries a marker block that is replaced
per request; every value is HTML-escaped, and JSON-LD is serialised with
``<``, ``>``, ``&`` and the two JS line separators escaped so no title can
close the script element.

Lookups are guarded because the shell answers any URL: a slug must match the
slug pattern before any query, answers (hits *and* misses) are cached for two
minutes, and only cache misses are charged to the per-IP public rate limit.
"""

from __future__ import annotations

import html
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

from structlog import get_logger

from .public_access import get_public_visibility

if TYPE_CHECKING:
    from fastapi import Request

    from .dependencies import AppState

logger = get_logger(__name__)

SITE_NAME = "Thestill"
DEFAULT_TITLE = "Thestill — read your podcasts"
DEFAULT_DESCRIPTION = "Podcast summaries you can read in three minutes, with every quote attributed and timed."
MARKER_RE = re.compile(r"<!--thestill:head-->.*?<!--/thestill:head-->", re.S)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,110}$")
_ENTITY_TYPES = ("person", "company", "product", "topic")
_LOOKUP_TTL_SECONDS = 120.0
_LOOKUP_CACHE_MAX = 4096
_DESCRIPTION_MAX = 160

# Paths a crawler may index. Everything else gets ``noindex``.
_PUBLIC_STATIC_PATHS = {"": "landing", "top": "top", "terms": "terms", "privacy": "privacy"}


@dataclass
class HeadTags:
    """What the shell injects for one URL."""

    title: str = DEFAULT_TITLE
    description: str = DEFAULT_DESCRIPTION
    canonical_path: Optional[str] = None
    og_type: str = "website"
    image: Optional[str] = None
    json_ld: Optional[Dict[str, Any]] = None
    index: bool = False
    status_code: int = 200
    extra: List[Tuple[str, str]] = field(default_factory=list)


class LookupThrottled(Exception):
    """The caller's public rate-limit bucket is empty (answer 429)."""


def _clip(text: Optional[str], limit: int = _DESCRIPTION_MAX) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _first_sentence(text: Optional[str]) -> str:
    text = (text or "").strip()
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return _clip(match.group(1) if match else text)


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_empty(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_drop_empty(v) for v in value if v not in (None, "")]
    return value


def json_ld_script(data: Dict[str, Any]) -> str:
    """A JSON-LD ``<script>`` that no string inside ``data`` can break out of."""
    body = json.dumps(_drop_empty(data), ensure_ascii=False, separators=(",", ":"))
    body = (
        body.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )
    return f'<script type="application/ld+json">{body}</script>'


def render_head(tags: HeadTags, base_url: Optional[str]) -> str:
    """The marker block's replacement for ``tags``."""
    e = lambda value: html.escape(value or "", quote=True)  # noqa: E731
    lines = [f"<title>{e(tags.title)}</title>", f'<meta name="description" content="{e(tags.description)}" />']
    if not tags.index:
        lines.append('<meta name="robots" content="noindex" />')
    canonical = f"{base_url}{tags.canonical_path}" if base_url and tags.canonical_path is not None else None
    if canonical:
        lines.append(f'<link rel="canonical" href="{e(canonical)}" />')
    image = tags.image or (f"{base_url}/og-default.png" if base_url else None)
    og = [
        ("og:site_name", SITE_NAME),
        ("og:type", tags.og_type),
        ("og:title", tags.title),
        ("og:description", tags.description),
    ]
    if canonical:
        og.append(("og:url", canonical))
    if image:
        og.append(("og:image", image))
    lines += [f'<meta property="{p}" content="{e(v)}" />' for p, v in og]
    lines.append('<meta name="twitter:card" content="summary_large_image" />')
    lines += [f'<meta name="{e(n)}" content="{e(v)}" />' for n, v in tags.extra]
    if tags.json_ld and tags.index:
        lines.append(json_ld_script(tags.json_ld))
    return "\n    ".join(lines)


def inject(template: str, head: str) -> str:
    """Replace the marker block (or, for an old build, prepend to ``</head>``)."""
    if MARKER_RE.search(template):
        return MARKER_RE.sub(lambda _m: head, template, count=1)
    template = re.sub(r"<title>.*?</title>", "", template, count=1, flags=re.S)
    return template.replace("</head>", f"    {head}\n  </head>", 1)


class ShellRenderer:
    """Builds head tags for public paths, with the spec #94 lookup guards."""

    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._cache: Dict[str, Tuple[float, HeadTags]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        config = self._state.config
        return bool(config.multi_user and config.public_pages_enabled)

    def head_for(self, path: str, request: "Request") -> HeadTags:
        """Tags for ``path`` (no leading slash, no query). Raises
        :class:`LookupThrottled` when a repository lookup is needed and the
        caller's public bucket is empty."""
        path = path.strip("/")
        if path in _PUBLIC_STATIC_PATHS:
            return self._static(_PUBLIC_STATIC_PATHS[path])
        parts = path.split("/")
        if parts[0] == "podcasts" and len(parts) in (2, 4) and (len(parts) == 2 or parts[2] == "episodes"):
            if not all(_SLUG_RE.match(p) for i, p in enumerate(parts) if i in (1, 3)):
                return HeadTags(status_code=404)
            return self._cached(path, request, lambda: self._podcast_or_episode(parts))
        if parts[0] == "entities" and len(parts) == 3:
            if parts[1] not in _ENTITY_TYPES or not _SLUG_RE.match(parts[2]):
                return HeadTags(status_code=404)
            return self._cached(path, request, lambda: self._entity(parts[1], parts[2]))
        # Signed-in app paths and anything unknown: default tags, never indexed.
        return HeadTags()

    # -- guards ---------------------------------------------------------

    def _cached(self, key: str, request: "Request", build) -> HeadTags:
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] <= _LOOKUP_TTL_SECONDS:
                return hit[1]
        from .middleware.rate_limit import allow_public_request

        if not allow_public_request(request):
            raise LookupThrottled()
        tags = build()
        with self._lock:
            if len(self._cache) >= _LOOKUP_CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (now, tags)
        return tags

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    # -- builders -------------------------------------------------------

    def _static(self, kind: str) -> HeadTags:
        if kind == "landing":
            return HeadTags(
                title=DEFAULT_TITLE,
                canonical_path="/",
                index=True,
                json_ld={
                    "@context": "https://schema.org",
                    "@type": "WebSite",
                    "name": SITE_NAME,
                    "url": self._state.config.public_base_url or None,
                },
            )
        if kind == "top":
            return HeadTags(
                title=f"Top podcasts · {SITE_NAME}",
                description="Today's Apple Podcasts chart by country, with summaries for the shows on thestill.",
                canonical_path="/top",
                index=True,
            )
        name = "Terms of Service" if kind == "terms" else "Privacy Policy"
        return HeadTags(title=f"{name} · {SITE_NAME}", canonical_path=f"/{kind}", index=False)

    def _podcast_or_episode(self, parts: List[str]) -> HeadTags:
        state = self._state
        visibility = get_public_visibility(state)
        if len(parts) == 2:
            row = state.repository.get_podcast_row_by_slug(parts[1])
            if not row or not visibility.podcast_is_public(row["id"]):
                return HeadTags(status_code=404)
            _, summarised = state.repository.list_public_episode_rows(podcast_id=row["id"], limit=1)
            title = row.get("title") or ""
            return HeadTags(
                title=f"{title} — episode summaries · {SITE_NAME}",
                description=_first_sentence(row.get("description")) or DEFAULT_DESCRIPTION,
                canonical_path=f"/podcasts/{parts[1]}",
                image=row.get("image_url"),
                index=summarised > 0,
                json_ld={
                    "@context": "https://schema.org",
                    "@type": "PodcastSeries",
                    "name": title,
                    "description": _clip(row.get("description"), 300),
                    "image": row.get("image_url"),
                    "author": {"@type": "Person", "name": row.get("author")} if row.get("author") else None,
                },
            )
        result = state.repository.get_episode_by_slug(parts[1], parts[3])
        if not result:
            return HeadTags(status_code=404)
        podcast, episode = result
        if not visibility.episode_is_public(podcast.id, episode):
            return HeadTags(status_code=404)
        return HeadTags(
            title=f"{episode.title} · {podcast.title} · {SITE_NAME}",
            description=_first_sentence(episode.summary_preview) or DEFAULT_DESCRIPTION,
            canonical_path=f"/podcasts/{parts[1]}/episodes/{parts[3]}",
            og_type="article",
            image=episode.image_url or podcast.image_url,
            index=True,
            json_ld={
                "@context": "https://schema.org",
                "@type": "PodcastEpisode",
                "name": episode.title,
                "description": _clip(episode.summary_preview, 300),
                "datePublished": episode.pub_date.isoformat() if episode.pub_date else None,
                "associatedMedia": (
                    {"@type": "MediaObject", "contentUrl": str(episode.audio_url)} if episode.audio_url else None
                ),
                "partOfSeries": {"@type": "PodcastSeries", "name": podcast.title},
            },
        )

    def _entity(self, entity_type: str, slug: str) -> HeadTags:
        from fastapi import HTTPException

        from .routes.api_public import public_entity_payload

        try:
            payload = public_entity_payload(self._state, entity_type, slug)
        except HTTPException:
            return HeadTags(status_code=404)
        entity = payload["entity"]
        enrichment = payload.get("enrichment") or {}
        name = entity["name"]
        featured = payload.get("featured_quote")
        is_person = entity_type == "person"
        if is_person:
            title = f"{name} — podcast quotes and appearances · {SITE_NAME}"
        else:
            title = f"{name} in podcasts — who's talking and what they said · {SITE_NAME}"
        if featured:
            description = _clip(
                f"\u201c{featured['text']}\u201d — {featured['speaker']['name']}, {featured['podcast']['title']}"
            )
        else:
            description = _clip(enrichment.get("headline") or entity.get("description")) or DEFAULT_DESCRIPTION
        same_as = [u for u in (enrichment.get("wikipedia_url"),) if u]
        if entity.get("wikidata_qid"):
            same_as.append(f"https://www.wikidata.org/wiki/{entity['wikidata_qid']}")
        return HeadTags(
            title=title,
            description=description,
            canonical_path=f"/entities/{entity_type}/{slug}",
            og_type="profile" if is_person else "website",
            image=enrichment.get("image_url"),
            index=bool(payload.get("indexable")),
            json_ld={
                "@context": "https://schema.org",
                "@type": "Person" if is_person else ("Organization" if entity_type == "company" else "Thing"),
                "name": name,
                "description": enrichment.get("headline") or entity.get("description"),
                "image": enrichment.get("image_url"),
                "sameAs": same_as,
            },
        )


# ---------------------------------------------------------------------------
# robots.txt and sitemap.xml
# ---------------------------------------------------------------------------

ROBOTS_PUBLIC = """User-agent: *
Allow: /$
Allow: /top
Allow: /podcasts/
Allow: /entities/
Allow: /api/auth/status
Allow: /api/public/
Disallow: /inbox
Disallow: /briefings
Disallow: /search
Disallow: /settings
Disallow: /episodes
Disallow: /status
Disallow: /queue
Disallow: /failed
Disallow: /login
Disallow: /api/
Disallow: /mcp
Sitemap: {base}/sitemap.xml
"""

ROBOTS_PRIVATE = "User-agent: *\nDisallow: /\n"

_SITEMAP_MAX_URLS = 40_000
_SITEMAP_TTL_SECONDS = 3600.0
_SITEMAP_PAGE = 1000


class SitemapBuilder:
    """``sitemap.xml`` from the same public predicate the API uses (cached 1 h)."""

    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._lock = threading.Lock()
        self._built_at = 0.0
        self._urls: List[Tuple[str, Optional[str]]] = []

    def _collect(self) -> List[Tuple[str, Optional[str]]]:
        repo = self._state.repository
        urls: List[Tuple[str, Optional[str]]] = [("/", None), ("/top", None)]
        podcasts: Dict[str, Optional[str]] = {}
        offset = 0
        while True:
            rows, total = repo.list_public_episode_rows(limit=_SITEMAP_PAGE, offset=offset)
            for row in rows:
                lastmod = row.get("published_at") or row.get("pub_date")
                lastmod_iso = (
                    lastmod.isoformat() if hasattr(lastmod, "isoformat") else (str(lastmod) if lastmod else None)
                )
                urls.append((f"/podcasts/{row['podcast_slug']}/episodes/{row['slug']}", lastmod_iso))
                current = podcasts.get(row["podcast_slug"])
                if lastmod_iso and (current is None or lastmod_iso > current):
                    podcasts[row["podcast_slug"]] = lastmod_iso
                else:
                    podcasts.setdefault(row["podcast_slug"], current)
            offset += len(rows)
            if not rows or offset >= total:
                break
        urls[2:2] = [(f"/podcasts/{slug}", lastmod) for slug, lastmod in sorted(podcasts.items())]
        # Person and entity pages with enough quotes to be worth indexing
        # (the same threshold the page uses for noindex).
        for row in self._state.entity_repository.public_quote_queries().indexable_entities(min_quotes=3):
            entity_id = str(row["entity_id"])
            slug = entity_id.split(":", 1)[1] if ":" in entity_id else entity_id
            entity_type = row["entity_type"].value if hasattr(row["entity_type"], "value") else row["entity_type"]
            urls.append((f"/entities/{entity_type}/{slug}", None))
        return urls

    def urls(self) -> List[Tuple[str, Optional[str]]]:
        now = time.monotonic()
        with self._lock:
            if self._urls and now - self._built_at <= _SITEMAP_TTL_SECONDS:
                return self._urls
        urls = self._collect()
        with self._lock:
            self._urls, self._built_at = urls, now
        return urls

    def render(self, page: Optional[int] = None) -> Optional[str]:
        """The sitemap (``page=None``), a sitemap index past 40k URLs, or one
        numbered part. ``None`` for a part that does not exist."""
        base = self._state.config.public_base_url
        urls = self.urls()
        parts = [urls[i : i + _SITEMAP_MAX_URLS] for i in range(0, len(urls), _SITEMAP_MAX_URLS)] or [[]]
        if page is None and len(parts) > 1:
            items = "".join(
                f"<sitemap><loc>{xml_escape(f'{base}/sitemap-{n}.xml')}</loc></sitemap>"
                for n in range(1, len(parts) + 1)
            )
            return f'<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</sitemapindex>'
        chosen = parts[0] if page is None else (parts[page - 1] if 1 <= page <= len(parts) else None)
        if chosen is None:
            return None
        entries = []
        for path, lastmod in chosen:
            loc = f"<loc>{xml_escape(base + path)}</loc>"
            entries.append(
                f"<url>{loc}<lastmod>{xml_escape(lastmod)}</lastmod></url>" if lastmod else f"<url>{loc}</url>"
            )
        return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{"".join(entries)}</urlset>'


_RENDERER_ATTR = "_shell_renderer"
_SITEMAP_ATTR = "_sitemap_builder"


def get_shell_renderer(state: "AppState") -> ShellRenderer:
    renderer = getattr(state, _RENDERER_ATTR, None)
    if renderer is None:
        renderer = ShellRenderer(state)
        setattr(state, _RENDERER_ATTR, renderer)
    return renderer


def get_sitemap_builder(state: "AppState") -> SitemapBuilder:
    builder = getattr(state, _SITEMAP_ATTR, None)
    if builder is None:
        builder = SitemapBuilder(state)
        setattr(state, _SITEMAP_ATTR, builder)
    return builder


class ShellTemplate:
    """``index.html`` read once and re-read when the build replaces it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime = -1.0
        self._text = ""
        self._lock = threading.Lock()

    def text(self) -> str:
        mtime = self._path.stat().st_mtime
        with self._lock:
            if mtime != self._mtime:
                self._text = self._path.read_text(encoding="utf-8")
                self._mtime = mtime
            return self._text
