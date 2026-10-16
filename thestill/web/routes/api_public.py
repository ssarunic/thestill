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

"""Signed-out read API for the public pages (spec #94), mounted at ``/api/public``.

Everything here is readable without a session, so the module is built to a
few hard rules:

- **Explicit shapes.** Every response is assembled field by field from an
  allowlist; nothing is splatted from a model or a row, so pipeline state,
  failure details, storage paths, feed URLs and per-user flags cannot leak.
- **No spend.** The module never imports the LLM provider factory, never
  enqueues work (no refresh-on-open) and never writes. A summary language
  that is not already stored is a 404, not a translation.
- **One visibility rule.** :mod:`thestill.web.public_access` decides what is
  public; hidden podcasts and entities are 404s here and are filtered out of
  every list.
- **Bounded.** Page sizes are capped and the router carries the per-IP
  public rate limit plus the public cache policy.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from structlog import get_logger

from ...repositories.public_quotes import QuoteFilter, days_ago
from ...services.podcast_service import PodcastWithIndex
from ...utils.duration import format_duration
from ...utils.geoip import lookup_country_from_ip
from ...utils.language_config import normalize_language_code
from ..dependencies import AppState, get_app_state
from ..middleware import resolve_client_ip
from ..public_access import (
    PublicVisibility,
    TtlCache,
    apply_public_cache_headers,
    get_public_visibility,
    public_rate_limit,
)
from ..responses import api_response, etag_json_response
from .api_entities import _entity_id_from_path, get_episode_entities
from .api_top_podcasts import _MAX_LIMIT as _TOP_MAX_LIMIT
from .api_top_podcasts import _resolve_region

logger = get_logger(__name__)

router = APIRouter(dependencies=[Depends(public_rate_limit)])

# Slugs come from python-slugify (lowercase [a-z0-9-], at most ~105 chars with
# a -N suffix). Anything else is a 404 before any query runs.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,110}$")
_ENTITY_TYPES = ("person", "company", "product", "topic")

_EPISODE_PAGE_MAX = 50
_RECENT_MAX = 12
_DEFAULT_TOP_REGION = "us"
_RECENT_TTL_SECONDS = 300.0


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def _check_slug(value: str, what: str) -> str:
    if not _SLUG_RE.match(value or ""):
        raise _not_found(what)
    return value


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _duration(value: Any) -> Optional[str]:
    try:
        return format_duration(value) if value else None
    except (TypeError, ValueError):
        return None


def _json_list(value: Any) -> List[str]:
    """``guest_entity_ids`` arrive as a list (Postgres jsonb) or JSON text (SQLite)."""
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return [str(v) for v in value if v]


# ---------------------------------------------------------------------------
# Entity references
# ---------------------------------------------------------------------------


_DISAMBIGUATION = re.compile(r"\s*\([^)]*\)$")


def display_name(name: Optional[str]) -> str:
    """``Amazon (company)`` → ``Amazon``: Wikidata-style disambiguation reads
    as noise on a page; the type is already shown beside the name."""
    return _DISAMBIGUATION.sub("", name or "").strip() or (name or "")


def entity_slug(entity_id: str) -> str:
    """URL slug of an entity: its id without the ``type:`` prefix.

    The prefix can differ from the current type after a re-type, so hrefs
    are always built from ``entity.type`` + this slug, never from the id.
    """
    return entity_id.split(":", 1)[1] if ":" in entity_id else entity_id


def _entity_refs(state: AppState, visibility: PublicVisibility, ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """``{id: ref}`` for the public entities among ``ids`` (hidden ones dropped)."""
    wanted = [i for i in dict.fromkeys(ids) if i]
    if not wanted:
        return {}
    hidden = visibility.hidden_entity_ids()
    images = state.entity_repository.get_entity_image_urls(wanted)
    refs: Dict[str, Dict[str, Any]] = {}
    for entity in state.entity_repository.get_entities_by_ids(wanted):
        if entity.id in hidden:
            continue
        refs[entity.id] = {
            "name": display_name(entity.canonical_name),
            "type": str(entity.type.value if hasattr(entity.type, "value") else entity.type),
            "slug": entity_slug(entity.id),
            "image_url": images.get(entity.id),
        }
    return refs


def _ordered(refs: Dict[str, Dict[str, Any]], ids: Iterable[str]) -> List[Dict[str, Any]]:
    return [refs[i] for i in dict.fromkeys(ids) if i in refs]


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def _episode_list_item(row: Dict[str, Any], guests: List[Dict[str, Any]], *, with_podcast: bool) -> Dict[str, Any]:
    item = {
        "slug": row["slug"],
        "title": row["title"],
        "pub_date": _iso(row.get("pub_date")),
        "duration_formatted": _duration(row.get("duration")),
        "image_url": row.get("image_url") or row.get("podcast_image_url"),
        "summary_preview": row.get("summary_preview") or None,
        "guests": guests,
    }
    if with_podcast:
        item["podcast"] = {
            "slug": row["podcast_slug"],
            "title": row["podcast_title"],
            "image_url": row.get("podcast_image_url"),
        }
    return item


def _episode_items(
    state: AppState, visibility: PublicVisibility, rows: List[Dict[str, Any]], *, with_podcast: bool
) -> List[Dict[str, Any]]:
    guest_ids = {row["id"]: _json_list(row.get("guest_entity_ids")) for row in rows}
    refs = _entity_refs(state, visibility, (g for ids in guest_ids.values() for g in ids))
    return [_episode_list_item(row, _ordered(refs, guest_ids[row["id"]]), with_podcast=with_podcast) for row in rows]


def _public_podcast_row(state: AppState, visibility: PublicVisibility, podcast_slug: str) -> Dict[str, Any]:
    _check_slug(podcast_slug, "Podcast")
    row = state.repository.get_podcast_row_by_slug(podcast_slug)
    if not row or not visibility.podcast_is_public(row["id"]):
        raise _not_found("Podcast")
    return row


def _public_episode(state: AppState, visibility: PublicVisibility, podcast_slug: str, episode_slug: str):
    _check_slug(podcast_slug, "Episode")
    _check_slug(episode_slug, "Episode")
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)
    if not result:
        raise _not_found("Episode")
    podcast, episode = result
    if not visibility.episode_is_public(podcast.id, episode):
        raise _not_found("Episode")
    return podcast, episode


# ---------------------------------------------------------------------------
# Podcasts and episodes
# ---------------------------------------------------------------------------


@router.get("/podcasts/{podcast_slug}")
def get_public_podcast(
    podcast_slug: str,
    request: Request,
    response: Response,
    state: AppState = Depends(get_app_state),
) -> dict:
    """A public show: metadata, platform links, hosts, summarised-episode count."""
    visibility = get_public_visibility(state)
    row = _public_podcast_row(state, visibility, podcast_slug)
    info = PodcastWithIndex(index=0, **row)
    anchors = state.entity_repository.get_podcast_anchors(info.id)
    refs = _entity_refs(state, visibility, [*anchors.get("hosts", []), *anchors.get("recurring", [])])
    _, summarised = state.repository.list_public_episode_rows(podcast_id=info.id, limit=1)
    apply_public_cache_headers(request, response)
    return api_response(
        {
            "podcast": {
                "slug": info.slug,
                "title": info.title,
                "description": info.description,
                "author": info.author,
                "image_url": info.image_url,
                "explicit": info.explicit,
                "copyright": info.copyright,
                "website_url": info.website_url,
                "primary_category": info.primary_category,
                "episodes_count": info.episodes_count,
                "summarised_count": summarised,
                "latest_pub_date": _iso(info.last_processed),
                "apple_url": info.apple_url,
                "spotify_url": info.spotify_url,
                "youtube_url": info.youtube_url,
                "hosts": _ordered(refs, anchors.get("hosts", [])),
                "recurring": _ordered(refs, anchors.get("recurring", [])),
            }
        }
    )


@router.get("/podcasts/{podcast_slug}/episodes")
def list_public_podcast_episodes(
    podcast_slug: str,
    request: Request,
    response: Response,
    limit: int = Query(10, ge=1, le=_EPISODE_PAGE_MAX),
    offset: int = Query(0, ge=0, le=10_000),
    state: AppState = Depends(get_app_state),
) -> dict:
    """A show's summarised episodes, newest first (only these have public pages)."""
    visibility = get_public_visibility(state)
    row = _public_podcast_row(state, visibility, podcast_slug)
    rows, total = state.repository.list_public_episode_rows(podcast_id=row["id"], limit=limit, offset=offset)
    apply_public_cache_headers(request, response)
    items = _episode_items(state, visibility, rows, with_podcast=False)
    next_offset = offset + len(items) if offset + len(items) < total else None
    return api_response(
        {"episodes": items, "total": total, "offset": offset, "limit": limit, "next_offset": next_offset}
    )


@router.get("/podcasts/{podcast_slug}/episodes/{episode_slug}")
def get_public_episode(
    podcast_slug: str,
    episode_slug: str,
    request: Request,
    response: Response,
    state: AppState = Depends(get_app_state),
) -> dict:
    """A public episode: header, publisher description, platform links, who's in it.

    ``has_transcript`` only says a transcript exists (behind sign-in); the
    transcript itself is never served here.
    """
    visibility = get_public_visibility(state)
    podcast, episode = _public_episode(state, visibility, podcast_slug, episode_slug)
    anchors = state.entity_repository.get_podcast_anchors(podcast.id)
    host_ids = list(anchors.get("hosts", [])) + list(anchors.get("recurring", []))
    episode_ids = state.entity_repository.get_episode_anchors(episode.id)
    guest_ids = [i for i in episode_ids if i not in set(host_ids)]
    refs = _entity_refs(state, visibility, [*host_ids, *guest_ids])
    platform_links = [
        {"platform": link.platform, "url": link.url} for link in state.repository.get_platform_links(episode.id)
    ]
    apply_public_cache_headers(request, response)
    return api_response(
        {
            "episode": {
                "slug": episode.slug,
                "title": episode.title,
                "description": episode.description,
                "pub_date": _iso(episode.pub_date),
                "duration_formatted": _duration(episode.duration),
                "image_url": episode.image_url or podcast.image_url,
                "explicit": episode.explicit,
                "episode_number": episode.episode_number,
                "season_number": episode.season_number,
                "website_url": episode.website_url,
                "audio_url": str(episode.audio_url) if episode.audio_url else None,
                "platform_links": platform_links,
                "has_transcript": bool(episode.clean_transcript_path),
                "hosts": _ordered(refs, host_ids),
                "guests": _ordered(refs, guest_ids),
                "podcast": {
                    "slug": podcast.slug,
                    "title": podcast.title,
                    "author": podcast.author,
                    "image_url": podcast.image_url,
                    "apple_url": getattr(podcast, "apple_url", None),
                    "spotify_url": getattr(podcast, "spotify_url", None),
                    "youtube_url": getattr(podcast, "youtube_url", None),
                },
            }
        }
    )


@router.get("/podcasts/{podcast_slug}/episodes/{episode_slug}/summary")
def get_public_episode_summary(
    podcast_slug: str,
    episode_slug: str,
    request: Request,
    lang: Optional[str] = Query(None, pattern=r"^[A-Za-z]{2,3}$"),
    state: AppState = Depends(get_app_state),
) -> Response:
    """The stored summary, in a stored language only (spec #94 cost guard).

    Never detects a language and never translates: an unrecorded canonical
    language falls back to the podcast's, and a ``lang`` that is not already
    stored is a 404. Citations are omitted (there is no public transcript to
    jump into).
    """
    visibility = get_public_visibility(state)
    podcast, episode = _public_episode(state, visibility, podcast_slug, episode_slug)
    podcast_language = normalize_language_code(podcast.language)
    canonical = normalize_language_code(
        state.podcast_service.get_recorded_summary_language(episode), default=podcast_language
    )
    available = state.podcast_service.get_available_summary_languages(episode, canonical_language=canonical)
    requested = normalize_language_code(lang, default=canonical) if lang else canonical
    if requested != canonical and requested not in available:
        raise _not_found("Summary language")
    summary = state.podcast_service.get_summary_for_episode(episode, language=requested, canonical_language=canonical)
    if not summary or summary.startswith("N/A"):
        raise _not_found("Summary")
    result = etag_json_response(
        request,
        {
            "content": summary,
            "language": requested,
            "canonical_language": canonical,
            "available_languages": available or [canonical],
        },
    )
    apply_public_cache_headers(request, result)
    return result


# ---------------------------------------------------------------------------
# Landing feed and chart
# ---------------------------------------------------------------------------

_recent_cache = TtlCache(_RECENT_TTL_SECONDS, max_size=_RECENT_MAX * 2)


@router.get("/recent-summaries")
def list_recent_summaries(
    request: Request,
    response: Response,
    limit: int = Query(6, ge=1, le=_RECENT_MAX),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Newest public summaries across the corpus (the landing page feed)."""
    visibility = get_public_visibility(state)

    def build() -> List[Dict[str, Any]]:
        rows, _ = state.repository.list_public_episode_rows(limit=limit, order_by="published_at")
        return _episode_items(state, visibility, rows, with_podcast=True)

    items = _recent_cache.get_or_compute((limit, visibility.fingerprint()), build)
    apply_public_cache_headers(request, response)
    return api_response({"episodes": items})


# A visitor's country by IP, kept a day per address so a returning visitor
# (or a crawler) costs one ipinfo call, not one per page view.
_visitor_country_cache = TtlCache(24 * 3600.0, max_size=10_000)


async def visitor_country(request: Request) -> Optional[str]:
    """The signed-out visitor's country (ISO code, lower case) from their IP,
    or None when the chart region was chosen explicitly or the lookup fails."""
    if request.query_params.get("region"):
        return None
    ip = resolve_client_ip(request)
    hit, country = _visitor_country_cache.lookup(ip)
    if not hit:
        country = await lookup_country_from_ip(ip)
        _visitor_country_cache.put(ip, country)
    return country


@router.get("/top-podcasts")
def list_public_top_podcasts(
    request: Request,
    response: Response,
    region: Optional[str] = Query(None, min_length=2, max_length=2),
    category: Optional[str] = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=_TOP_MAX_LIMIT),
    country: Optional[str] = Depends(visitor_country),
    state: AppState = Depends(get_app_state),
) -> dict:
    """The chart for one region: ``?region=``, else the visitor's country by
    IP, else the US. Rows link to a show page only when the show is public."""
    visibility = get_public_visibility(state)
    available = state.repository.get_top_podcast_regions()
    preferred = country if country in available else _DEFAULT_TOP_REGION
    resolved = _resolve_region(region, preferred, available)
    category_clean = category.strip() if category and category.strip() else None
    rows = state.repository.get_top_podcasts(resolved, limit=limit, q=None, category=category_clean, user_id=None)
    hidden_slugs = visibility.hidden_podcast_slugs()
    items = [
        {
            "rank": row.get("rank"),
            "name": row.get("name"),
            "artist": row.get("artist"),
            "image_url": row.get("image_url"),
            "category": row.get("category"),
            "apple_url": row.get("apple_url"),
            "podcast_slug": row.get("podcast_slug") if row.get("podcast_slug") not in hidden_slugs else None,
        }
        for row in rows
    ]
    apply_public_cache_headers(request, response)
    if not region:
        # Picked from the visitor's IP: a shared cache must not hand this
        # country's chart to someone in another.
        response.headers["Cache-Control"] = "private, max-age=300"
    return api_response(
        {
            "region": resolved,
            "available_regions": available,
            "available_categories": state.repository.get_top_podcast_categories(resolved),
            "top_podcasts": items,
        }
    )


# ---------------------------------------------------------------------------
# People, entities and quotes
# ---------------------------------------------------------------------------

_QUOTE_PAGE = 12
_MENTIONED_PER_TYPE = 20
_RELATED_PER_TYPE = 6
_FEATURED_WINDOW_DAYS = 90
_SPEAKERS_WINDOW_DAYS = 365
_ACTIVITY_WEEKS = 26
_INDEX_MIN_QUOTES = 3
_ENTITY_TTL_SECONDS = 120.0
_ABOUT_RE = re.compile(r"^(person|company|product|topic):[a-z0-9][a-z0-9-]{0,110}$")

_entity_cache = TtlCache(_ENTITY_TTL_SECONDS, max_size=512)


def _per_type(items: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """The first ``cap`` items of each ``type``, order kept (pages group by type)."""
    seen: Dict[str, int] = {}
    kept = []
    for item in items:
        seen[item["type"]] = seen.get(item["type"], 0) + 1
        if seen[item["type"]] <= cap:
            kept.append(item)
    return kept


def _entity_type_str(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _quote_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """One public quote, attributed and timed (no transcript beyond the excerpt)."""
    start_ms = int(row.get("start_ms") or 0)
    return {
        "text": (row.get("quote") or "").strip(),
        "start_seconds": start_ms // 1000,
        "speaker": {"name": display_name(row["speaker_name"]), "slug": entity_slug(str(row["speaker_id"]))},
        "about": {
            "name": display_name(row["about_name"]),
            "type": _entity_type_str(row["about_type"]),
            "slug": entity_slug(str(row["about_id"])),
            "id": str(row["about_id"]),
        },
        "episode": {
            "slug": row["episode_slug"],
            "title": row["episode_title"],
            "pub_date": _iso(row.get("episode_pub_date")),
            "image_url": row.get("episode_image_url") or row.get("podcast_image_url"),
        },
        "podcast": {"slug": row["podcast_slug"], "title": row["podcast_title"]},
    }


_DANGLING_OPENERS = re.compile(
    r"^(he|she|it|they|them|him|her|his|their|this|that|these|those|and|but|so|or|because|which|who|like|yeah|well)\b",
    re.I,
)


def _stands_alone(text: str) -> bool:
    """A featured quote should read on its own: it opens a sentence (capital,
    no pronoun or connective) and ends one."""
    text = text.strip().strip('\u201c\u201d"')
    return bool(text) and text[0].isupper() and not _DANGLING_OPENERS.match(text) and text[-1] in ".!?"


def _public_entity_id(state: AppState, visibility: PublicVisibility, entity_type: str, slug: str) -> str:
    if entity_type not in _ENTITY_TYPES:
        raise _not_found("Entity")
    _check_slug(slug, "Entity")
    try:
        entity_id = _entity_id_from_path(state.entity_repository, entity_type, slug)
    except HTTPException:
        raise _not_found("Entity") from None
    if not visibility.entity_is_public(entity_id):
        raise _not_found("Entity")
    return entity_id


def _parse_date(value: Any):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _activity(presence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Episodes per week (Monday-based) for the last 26 weeks, oldest first."""
    today = datetime.now(timezone.utc).date()
    first_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=_ACTIVITY_WEEKS - 1)
    weeks = [0] * _ACTIVITY_WEEKS
    for row in presence:
        pub = _parse_date(row.get("pub_date"))
        if pub is None:
            continue
        index = (pub.date() - first_monday).days // 7
        if 0 <= index < _ACTIVITY_WEEKS:
            weeks[index] += 1
    return {"start": first_monday.isoformat(), "weeks": weeks}


def _stats(presence: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Hero counts: shows and episodes where the entity speaks or is named."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    dated = [(str(r["episode_id"]), _parse_date(r.get("pub_date"))) for r in presence]
    dates = [d for _, d in dated if d is not None]
    return {
        "shows": len({str(r["podcast_id"]) for r in presence}),
        "episodes": len({episode for episode, _ in dated}),
        "since": min(dates).date().isoformat() if dates else None,
        "last_30_days": len({episode for episode, d in dated if d is not None and d >= cutoff}),
    }


def _build_entity_payload(state: AppState, visibility: PublicVisibility, entity_id: str) -> Dict[str, Any]:
    repo = state.entity_repository
    summary = repo.get_entity_summary(entity_id, recent_mentions_limit=1, recent_mentions_per_episode=1)
    if summary is None:
        raise _not_found("Entity")
    record = summary["entity"]
    entity_type = _entity_type_str(record.type)
    queries = repo.public_quote_queries()
    hidden_podcasts = visibility.hidden_podcast_ids()
    hidden_entities = visibility.hidden_entity_ids()
    is_person = entity_type == "person"

    said_total = 0
    if is_person:
        _, said_total = queries.quotes(QuoteFilter(speaker_id=entity_id), limit=1)
    _, about_total = queries.quotes(QuoteFilter(about_id=entity_id, exclude_speaker_id=entity_id), limit=1)

    def featured(f_recent: QuoteFilter, f_any: QuoteFilter) -> Optional[Dict[str, Any]]:
        for f in (f_recent, f_any):
            rows, _ = queries.quotes(f, order="longest", limit=25)
            standalone = [r for r in rows if _stands_alone(r.get("quote") or "")]
            if standalone or rows:
                return _quote_item((standalone or rows)[0])
        return None

    since = days_ago(_FEATURED_WINDOW_DAYS)
    if is_person and said_total:
        featured_quote = featured(QuoteFilter(speaker_id=entity_id, since=since), QuoteFilter(speaker_id=entity_id))
    elif about_total:
        featured_quote = featured(
            QuoteFilter(about_id=entity_id, exclude_speaker_id=entity_id, since=since),
            QuoteFilter(about_id=entity_id, exclude_speaker_id=entity_id),
        )
    else:
        featured_quote = None

    topics = []
    if is_person:
        for row in queries.topics(entity_id, limit=8):
            if str(row["about_id"]) in hidden_entities:
                continue
            topics.append(
                {
                    "id": str(row["about_id"]),
                    "name": display_name(row["about_name"]),
                    "type": _entity_type_str(row["about_type"]),
                    "slug": entity_slug(str(row["about_id"])),
                    "quotes": int(row["quotes"]),
                }
            )

    speakers = []
    if not is_person or about_total:
        speaker_rows = queries.speakers(entity_id, since=days_ago(_SPEAKERS_WINDOW_DAYS), limit=8)
        speaker_images = state.entity_repository.get_entity_image_urls([str(r["speaker_id"]) for r in speaker_rows])
        for row in speaker_rows:
            best, _ = queries.quotes(
                QuoteFilter(speaker_id=str(row["speaker_id"]), about_id=entity_id), order="longest", limit=10
            )
            best = sorted(best, key=lambda r: not _stands_alone(r.get("quote") or ""))
            speakers.append(
                {
                    "name": display_name(row["speaker_name"]),
                    "slug": entity_slug(str(row["speaker_id"])),
                    "quotes": int(row["quotes"]),
                    "shows": int(row["shows"]),
                    "image_url": speaker_images.get(str(row["speaker_id"])),
                    "quote": _quote_item(best[0])["text"] if best else None,
                }
            )

    presence = queries.presence(entity_id)

    def podcast_refs(rows: Iterable[Dict[str, Any]], count_key: str) -> List[Dict[str, Any]]:
        return [
            {"slug": r.get("podcast_slug"), "title": r["podcast_title"], "count": r.get(count_key)}
            for r in rows
            if r.get("podcast_slug")
            and str(r["podcast_id"]) not in hidden_podcasts
            and visibility.podcast_is_public(r["podcast_id"])
        ]

    related = []
    for row in summary.get("cooccurring", []):
        other = row["entity"]
        if other.id in hidden_entities:
            continue
        related.append(
            {
                "name": display_name(other.canonical_name),
                "type": _entity_type_str(other.type),
                "slug": entity_slug(other.id),
                "episode_count": row["episode_count"],
            }
        )

    enrichment = summary.get("enrichment")
    enrichment_wire = None
    if enrichment is not None and enrichment.has_content():
        affiliations = []
        for aff in enrichment.affiliations or []:
            if aff.entity_id in hidden_entities:
                continue  # A hidden entity's name must not appear on other pages either.
            ref = None
            if aff.entity_id and aff.entity_type:
                ref = {"type": _entity_type_str(aff.entity_type), "slug": entity_slug(aff.entity_id)}
            affiliations.append({"relation": aff.relation, "label": aff.label, "ref": ref})
        enrichment_wire = {
            "headline": enrichment.headline,
            "image_url": enrichment.image_url,
            "image_attribution": enrichment.image_attribution,
            "wikipedia_extract": enrichment.wikipedia_extract,
            "wikipedia_url": enrichment.wikipedia_url,
            "facts": [{"label": f.label, "value": f.value, "url": f.url} for f in (enrichment.facts or [])][:6],
            "affiliations": affiliations[:8],
        }

    guest_rows = [r for r in summary.get("guest_episodes", []) if r.get("podcast_slug") and r.get("episode_slug")]
    guest_lookup = state.repository.get_episodes_by_ids([str(r["episode_id"]) for r in guest_rows])
    guest_episodes = [
        {
            "podcast_slug": r["podcast_slug"],
            "podcast_title": r["podcast_title"],
            "episode_slug": r["episode_slug"],
            "episode_title": r["episode_title"],
            "published_at": _iso(r.get("published_at")),
        }
        for r in guest_rows
        # The same predicate as an episode page: summarised, on a public show.
        if str(r["episode_id"]) in guest_lookup
        and visibility.episode_is_public(r["podcast_id"], guest_lookup[str(r["episode_id"])][1])
    ][:12]

    return {
        "entity": {
            "name": display_name(record.canonical_name),
            "type": entity_type,
            "slug": entity_slug(record.id),
            "description": record.description,
            "wikidata_qid": record.wikidata_qid,
        },
        "enrichment": enrichment_wire,
        "stats": _stats(presence),
        "activity": _activity(presence),
        "roles": {
            "hosts": podcast_refs(summary.get("hosts_podcasts", []), "episode_count"),
            "recurring": podcast_refs(summary.get("recurring_podcasts", []), "episode_count"),
            "guest_episodes": guest_episodes,
        },
        "most_discussed_on": podcast_refs(summary.get("most_discussed_on", []), "mention_count")[:8],
        "related": _per_type(related, _RELATED_PER_TYPE),
        "featured_quote": featured_quote,
        "topics": topics,
        "speakers": speakers,
        "quote_counts": {"said": said_total, "about": about_total},
        "indexable": (said_total + about_total) >= _INDEX_MIN_QUOTES,
    }


def public_entity_payload(state: AppState, entity_type: str, slug: str) -> Dict[str, Any]:
    """The cached public entity payload (also used by the page shell's head tags)."""
    visibility = get_public_visibility(state)
    entity_id = _public_entity_id(state, visibility, entity_type, slug)
    return _entity_cache.get_or_compute(
        (entity_id, visibility.fingerprint()), lambda: _build_entity_payload(state, visibility, entity_id)
    )


@router.get("/entities/{entity_type}/{slug}")
def get_public_entity(
    entity_type: str,
    slug: str,
    request: Request,
    response: Response,
    state: AppState = Depends(get_app_state),
) -> dict:
    """A person or entity page: who they are, where they're heard, the
    featured quote, topics (people) or who's talking (things)."""
    payload = public_entity_payload(state, entity_type, slug)
    apply_public_cache_headers(request, response)
    return api_response(payload)


@router.get("/entities/{entity_type}/{slug}/quotes")
def list_public_entity_quotes(
    entity_type: str,
    slug: str,
    request: Request,
    response: Response,
    kind: str = Query("about", pattern="^(said|about)$"),
    about: Optional[str] = Query(None, max_length=120),
    page: int = Query(1, ge=1, le=200),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Quote stream: ``said`` = quotes the person said (optionally ``about``
    one entity id); ``about`` = quotes by other named speakers that name it."""
    visibility = get_public_visibility(state)
    entity_id = _public_entity_id(state, visibility, entity_type, slug)
    if about is not None and (not _ABOUT_RE.match(about) or not visibility.entity_is_public(about)):
        raise _not_found("Topic")
    if kind == "said":
        f = QuoteFilter(speaker_id=entity_id, about_id=about)
    else:
        f = QuoteFilter(about_id=entity_id, exclude_speaker_id=entity_id)
    rows, total = state.entity_repository.public_quote_queries().quotes(
        f, order="recent", limit=_QUOTE_PAGE, offset=(page - 1) * _QUOTE_PAGE
    )
    apply_public_cache_headers(request, response)
    return api_response(
        {
            "quotes": [_quote_item(r) for r in rows],
            "total": total,
            "page": page,
            "next_page": page + 1 if page * _QUOTE_PAGE < total else None,
        }
    )


@router.get("/podcasts/{podcast_slug}/episodes/{episode_slug}/quotes")
def list_public_episode_quotes(
    podcast_slug: str,
    episode_slug: str,
    request: Request,
    response: Response,
    q: Optional[int] = Query(None, ge=0, le=60 * 60 * 12, description="Start second of a linked quote"),
    speaker: Optional[str] = Query(None, max_length=120, description="Only quotes by this person (slug)"),
    page: int = Query(1, ge=1, le=200),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Quotes from one episode in episode order, a page at a time, plus who
    speaks in it and the entities it mentions.

    With ``q``, the quote starting within 5 seconds of it is returned as
    ``linked`` wherever it falls in the episode.
    """
    visibility = get_public_visibility(state)
    _podcast, episode = _public_episode(state, visibility, podcast_slug, episode_slug)
    every = _episode_quote_cache.get_or_compute(
        (str(episode.id), visibility.fingerprint()), lambda: _all_episode_quotes(state, str(episode.id))
    )
    shown = [item for item in every if not speaker or item["speaker"]["slug"] == speaker]
    start = (page - 1) * _QUOTE_PAGE
    linked = None
    if q is not None:
        near = [item for item in every if abs(item["start_seconds"] - q) <= 5]
        linked = min(near, key=lambda item: abs(item["start_seconds"] - q)) if near else None
    speakers: Dict[str, Dict[str, Any]] = {}
    for item in every:
        entry = speakers.setdefault(item["speaker"]["slug"], {**item["speaker"], "quotes": 0})
        entry["quotes"] += 1
    hidden = visibility.hidden_entity_ids()
    # Grouped by type on the page (people, companies, products, topics, as
    # in the signed-in reader), so the cap is per type, in the reader's
    # order: hosts and guests first, then by mentions.
    mentioned = []
    per_type: Dict[str, int] = {}
    for row in get_episode_entities(episode.id, min_confidence=0.0, state=state).entities:
        kind = _entity_type_str(row.entity.type)
        if row.entity.id in hidden or per_type.get(kind, 0) >= _MENTIONED_PER_TYPE:
            continue
        per_type[kind] = per_type.get(kind, 0) + 1
        mentioned.append(
            {
                "name": display_name(row.entity.canonical_name),
                "type": kind,
                "slug": entity_slug(row.entity.id),
                "count": row.mention_count,
            }
        )
    apply_public_cache_headers(request, response)
    return api_response(
        {
            "quotes": shown[start : start + _QUOTE_PAGE],
            "total": len(shown),
            "page": page,
            "next_page": page + 1 if start + _QUOTE_PAGE < len(shown) else None,
            "linked": linked,
            "speakers": sorted(speakers.values(), key=lambda s: -s["quotes"]),
            "mentioned": mentioned,
        }
    )


_EPISODE_QUOTES_MAX = 1000
_episode_quote_cache = TtlCache(_ENTITY_TTL_SECONDS, max_size=256)


def _all_episode_quotes(state: AppState, episode_id: str) -> List[Dict[str, Any]]:
    """Every public quote in one episode, in episode order (a few hundred at most)."""
    rows, _ = state.entity_repository.public_quote_queries().quotes(
        QuoteFilter(episode_id=episode_id), order="episode", limit=_EPISODE_QUOTES_MAX
    )
    return [_quote_item(r) for r in rows]


_TRENDING_TTL_SECONDS = 1800.0
_trending_cache = TtlCache(_TRENDING_TTL_SECONDS, max_size=4)


@router.get("/who-said-what")
def get_who_said_what(
    request: Request,
    response: Response,
    state: AppState = Depends(get_app_state),
) -> dict:
    """The landing's "Who said what this week": the subject discussed by the
    most distinct named speakers across the most shows in the last 7 days
    (30 if the week is quiet), with up to three quotes from different
    speakers on different shows."""
    visibility = get_public_visibility(state)
    payload = _trending_cache.get_or_compute(visibility.fingerprint(), lambda: _pick_trending(state))
    apply_public_cache_headers(request, response)
    return api_response(payload)


def _pick_trending(state: AppState) -> Dict[str, Any]:
    queries = state.entity_repository.public_quote_queries()
    payload = {"entity": None, "days": 7, "shows": 0, "speakers": 0, "quotes": []}
    for days in (7, 30):
        top = queries.trending(since=days_ago(days), limit=1)
        if not top:
            continue
        row = top[0]
        about_id = str(row["about_id"])
        rows, _ = queries.quotes(QuoteFilter(about_id=about_id, since=days_ago(days)), order="longest", limit=40)
        rows = sorted(rows, key=lambda r: not _stands_alone(r.get("quote") or ""))
        picked: List[Dict[str, Any]] = []
        seen_speakers: set = set()
        seen_shows: set = set()
        for quote_row in rows:
            speaker, show = str(quote_row["speaker_id"]), quote_row["podcast_slug"]
            if speaker in seen_speakers or show in seen_shows:
                continue
            picked.append(_quote_item(quote_row))
            seen_speakers.add(speaker)
            seen_shows.add(show)
            if len(picked) == 3:
                break
        payload = {
            "entity": {
                "name": display_name(row["about_name"]),
                "type": _entity_type_str(row["about_type"]),
                "slug": entity_slug(about_id),
            },
            "days": days,
            "shows": int(row["shows"]),
            "speakers": int(row["speakers"]),
            "quotes": picked,
        }
        break
    return payload
