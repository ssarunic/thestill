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

Crawlers and link unfurlers get real titles, descriptions, canonical URLs,
Open Graph tags and JSON-LD from here; the page's main content for
signed-out requests comes from :mod:`thestill.web.public_prerender`
(spec #105). ``index.html`` carries a marker block that is replaced
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
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple
from xml.sax.saxutils import escape as xml_escape

import structlog
from structlog import get_logger

from ..utils.user_agent import crawler_family
from .middleware.rate_limit import allow_public_request
from .public_access import (
    EntityIndexFacts,
    TtlCache,
    entity_is_indexable,
    entity_noindex_ids,
    get_public_visibility,
    has_valid_session,
    instance_of_list,
)

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


def shell_kind(path: str) -> Optional[str]:
    """``landing``, ``top``, ``podcast``, ``episode`` or ``entity`` for a
    public content path; ``None`` for everything else."""
    parts = path.strip("/").split("/")
    if parts == [""]:
        return "landing"
    if parts == ["top"]:
        return "top"
    if parts[0] == "podcasts" and len(parts) == 2:
        return "podcast"
    if parts[0] == "podcasts" and len(parts) == 4 and parts[2] == "episodes":
        return "episode"
    if parts[0] == "entities" and len(parts) == 3:
        return "entity"
    return None


def log_shell_served(
    path: str,
    status: int,
    user_agent: Optional[str],
    started: float,
    prerendered: str = "false",
    cache: Optional[str] = None,
) -> None:
    """Spec #105 §5: one line per public shell response, so crawler fetches
    can be counted per family. No IP and no full user agent: the request
    context's ``client_ip`` (bound by ``LoggingMiddleware``) is held back
    for this one event. The user agent is read here, after the response is
    built, and nowhere on the way to it (§2.1)."""
    kind = shell_kind(path)
    if kind is None:
        return
    client_ip = structlog.contextvars.get_contextvars().get("client_ip")
    structlog.contextvars.unbind_contextvars("client_ip")
    try:
        logger.info(
            "public_shell.served",
            kind=kind,
            status=status,
            ua_family=crawler_family(user_agent),
            prerendered=prerendered,
            cache=cache,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
    finally:
        if client_ip is not None:
            structlog.contextvars.bind_contextvars(client_ip=client_ip)


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
    #: With a 301 ``status_code``: where the page lives now.
    redirect_path: Optional[str] = None


class LookupThrottled(Exception):
    """The caller's public rate-limit bucket is empty (answer 429)."""


class LookupShed(Exception):
    """An uncached lookup found every prerender slot taken (spec #105 §4):
    answer 503 with ``Retry-After`` rather than build it."""


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


def safe_json(data: Any) -> str:
    """JSON for a ``<script>`` data block that no string inside ``data`` can
    break out of: ``<``, ``>``, ``&`` and the two JS line separators are
    escaped (the JSON-LD block and the spec #105 bootstrap block)."""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        body.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def json_ld_script(data: Dict[str, Any]) -> str:
    """A JSON-LD ``<script>`` that no string inside ``data`` can break out of."""
    return f'<script type="application/ld+json">{safe_json(_drop_empty(data))}</script>'


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
        self._cache = TtlCache(_LOOKUP_TTL_SECONDS, max_size=_LOOKUP_CACHE_MAX)

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
        """Cached tags for ``key``; a miss costs an anonymous caller one
        public-bucket token (a signed-in user is never refused the app)."""
        cache_key = (key, get_public_visibility(self._state).fingerprint())
        if not self._cache.peek(cache_key) and not has_valid_session(request) and not allow_public_request(request):
            raise LookupThrottled()
        return self._cache.get_or_compute(cache_key, build)

    @staticmethod
    def is_lookup_path(path: str) -> bool:
        """True for paths whose tags come from a repository lookup."""
        return path.strip("/").split("/")[0] in ("podcasts", "entities")

    def clear(self) -> None:
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
        """Head tags from the cached slug lookups the public API shares
        (spec #105, FM-6). Deliberately not the full payloads: those are
        built for the page body only, under the prerender concurrency cap,
        so a shed request costs one lookup."""
        from fastapi import HTTPException

        from .routes.api_public import public_episode_models, public_podcast_row

        state = self._state
        try:
            if len(parts) == 2:
                row = public_podcast_row(state, parts[1])
            else:
                podcast, episode = public_episode_models(state, parts[1], parts[3])
        except HTTPException:
            return HeadTags(status_code=404)
        if len(parts) == 2:
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

        from .public_access import CacheMiss, cache_only
        from .public_prerender import get_prerenderer
        from .routes.api_public import current_entity_path, public_entity_payload

        # The entity payload (quote counts decide noindex) is the costly part
        # of this page. Cached, it is free; uncached, it is built under the
        # prerender cap like the page body (spec #105 §4), and over the cap
        # the page is shed with a 503 instead of guessing ``noindex``. With
        # the prerender switch off the cap does not apply, as before #105.
        prerenderer = get_prerenderer(self._state)
        try:
            if not prerenderer.enabled():
                payload = public_entity_payload(self._state, entity_type, slug)
            else:
                try:
                    with cache_only():
                        payload = public_entity_payload(self._state, entity_type, slug)
                except CacheMiss:
                    with prerenderer.slot() as acquired:
                        if not acquired:
                            raise LookupShed() from None
                        payload = public_entity_payload(self._state, entity_type, slug)
        except HTTPException:
            return HeadTags(status_code=404)
        # A re-typed entity answers at its old type's URL too; send that URL
        # on, or the two copies split the page in search.
        moved_to = current_entity_path(self._state, entity_type, slug, payload)
        if moved_to:
            return HeadTags(status_code=301, redirect_path=moved_to)
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

# Spec #105 decision 1: crawlers that collect training data get nothing.
# Search and user-fetch agents (Bingbot, OAI-SearchBot, ChatGPT-User,
# Claude-SearchBot, Claude-User, PerplexityBot, Perplexity-User) send
# readers back, so they fall through to the ``*`` group.
TRAINING_CRAWLERS = (
    "GPTBot",
    "ClaudeBot",
    "CCBot",
    "Google-Extended",
    "Applebot-Extended",
    "Meta-ExternalAgent",
    "Bytespider",
)

ROBOTS_PUBLIC = (
    "".join(f"User-agent: {agent}\n" for agent in TRAINING_CRAWLERS)
    + "Disallow: /\n\n"
    + """User-agent: *
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
)

ROBOTS_PRIVATE = "User-agent: *\nDisallow: /\n"


def _json_ids(value: Any) -> Iterable[str]:
    """``guest_entity_ids`` arrive as a list (Postgres jsonb) or JSON text (SQLite)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ()
    return [str(v) for v in value or () if v]


_SITEMAP_MAX_URLS = 40_000
_SITEMAP_TTL_SECONDS = 3600.0
_SITEMAP_PAGE = 1000


class SitemapBuilder:
    """``sitemap.xml`` from the same public predicate the API uses (cached 1 h)."""

    def __init__(self, state: "AppState") -> None:
        self._state = state
        self._cache = TtlCache(_SITEMAP_TTL_SECONDS, max_size=2)

    def _collect(self) -> List[Tuple[str, Optional[str]]]:
        repo = self._state.repository
        urls: List[Tuple[str, Optional[str]]] = [("/", None), ("/top", None)]
        podcasts: Dict[str, Optional[str]] = {}
        podcast_ids: Set[str] = set()
        guest_ids: Set[str] = set()
        offset = 0
        while True:
            rows, total = repo.list_public_episode_rows(limit=_SITEMAP_PAGE, offset=offset)
            for row in rows:
                podcast_ids.add(str(row["podcast_id"]))
                guest_ids.update(_json_ids(row.get("guest_entity_ids")))
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
        role_ids = set(guest_ids)
        for podcast_id in sorted(podcast_ids):
            anchors = self._state.entity_repository.get_podcast_anchors(podcast_id)
            role_ids.update(str(i) for i in [*anchors.get("hosts", []), *anchors.get("recurring", [])] if i)
        urls.extend((path, None) for path in self._entity_paths(role_ids))
        return urls

    def _entity_paths(self, role_ids: Set[str]) -> List[str]:
        """Person and entity pages the indexing rule keeps (spec #105
        decision 3, the same :func:`entity_is_indexable` the page uses for
        noindex), each listed only if its URL shows that entity: a re-typed
        entity can share a URL with another one, and the page resolves it
        to the other."""
        from .routes.api_entities import url_candidate_ids, url_candidates, url_owner

        repo = self._state.entity_repository
        noindex = entity_noindex_ids(self._state)
        rows = []
        for row in repo.public_quote_queries().entity_index_facts():
            entity_id = str(row["entity_id"])
            slug = entity_id.split(":", 1)[1] if ":" in entity_id else entity_id
            entity_type = row["entity_type"].value if hasattr(row["entity_type"], "value") else row["entity_type"]
            facts = EntityIndexFacts(
                entity_id=entity_id,
                entity_type=str(entity_type),
                said=int(row["said"] or 0),
                about=int(row["about"] or 0),
                episodes=int(row["episodes"] or 0),
                has_role=entity_id in role_ids,
                wikidata_qid=row.get("wikidata_qid"),
                instance_of=instance_of_list(row.get("wikidata_instance_of")),
            )
            if entity_is_indexable(facts, noindex):
                rows.append((entity_id, entity_type, slug))
        # A role qualifies a page on its own, so a host or guest with no
        # resolved mention (absent from the facts above) is a candidate too.
        role_only = self._role_only_rows(role_ids, {entity_id for entity_id, _, _ in rows}, noindex)
        rows.extend(role_only)
        # A row from the facts has mentions, so one with the URL's own
        # prefix owns its URL; a re-typed row or a role-only row (no
        # mentions) can lose it to another entity.
        unsure = {entity_id for entity_id, _, _ in role_only}
        retyped_slugs = sorted(
            {
                slug
                for entity_id, entity_type, slug in rows
                if entity_id != f"{entity_type}:{slug}" or entity_id in unsure
            }
        )
        candidate_ids = [cid for slug in retyped_slugs for cid in url_candidate_ids(slug)]
        stored = []
        for start in range(0, len(candidate_ids), _SITEMAP_PAGE):
            stored.extend(repo.get_entities_by_ids(candidate_ids[start : start + _SITEMAP_PAGE]))
        by_slug: Dict[str, list] = {}
        for entity in stored:
            by_slug.setdefault(entity.id.split(":", 1)[1], []).append(entity)
        with_mentions = repo.entity_ids_with_mentions([entity.id for entity in stored])
        paths, collisions = [], []
        for entity_id, entity_type, slug in rows:
            if entity_id != f"{entity_type}:{slug}" or entity_id in unsure:
                candidates = url_candidates(entity_type, slug, by_slug.get(slug, []))
                if not candidates or url_owner(candidates, with_mentions) != entity_id:
                    collisions.append(entity_id)
                    continue
            paths.append(f"/entities/{entity_type}/{slug}")
        if collisions:
            # A data problem to fix (merge or delete the loser), not a crash.
            logger.warning("sitemap.entity_url_collisions", count=len(collisions), entity_ids=collisions[:20])
        return paths

    def _role_only_rows(self, role_ids: Set[str], seen: Set[str], noindex: Set[str]) -> List[Tuple[str, str, str]]:
        """``(entity_id, type, slug)`` for public role holders the facts query
        did not return (no resolved mention), judged by the same rule."""
        repo = self._state.entity_repository
        hidden = get_public_visibility(self._state).hidden_entity_ids()
        missing = sorted(role_ids - seen - hidden)
        rows = []
        for start in range(0, len(missing), _SITEMAP_PAGE):
            for entity in repo.get_entities_by_ids(missing[start : start + _SITEMAP_PAGE]):
                entity_type = str(entity.type.value if hasattr(entity.type, "value") else entity.type)
                facts = EntityIndexFacts(
                    entity_id=entity.id,
                    entity_type=entity_type,
                    said=0,
                    about=0,
                    episodes=0,
                    has_role=True,
                    wikidata_qid=entity.wikidata_qid,
                    instance_of=instance_of_list(entity.wikidata_instance_of),
                )
                if entity_is_indexable(facts, noindex):
                    rows.append((entity.id, entity_type, entity.id.split(":", 1)[1] if ":" in entity.id else entity.id))
        return rows

    def urls(self) -> List[Tuple[str, Optional[str]]]:
        # Keyed on the visibility fingerprint: a takedown drops its URLs on
        # the next hidden-set refresh, not an hour later.
        return self._cache.get_or_compute(get_public_visibility(self._state).fingerprint(), self._collect)

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
