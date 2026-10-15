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
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from structlog import get_logger

from ...services.podcast_service import PodcastWithIndex
from ...utils.duration import format_duration
from ...utils.language_config import normalize_language_code
from ..dependencies import AppState, get_app_state
from ..public_access import PublicVisibility, apply_public_cache_headers, get_public_visibility, public_rate_limit
from ..responses import api_response, etag_json_response
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
    refs: Dict[str, Dict[str, Any]] = {}
    for entity in state.entity_repository.get_entities_by_ids(wanted):
        if entity.id in hidden:
            continue
        refs[entity.id] = {
            "name": entity.canonical_name,
            "type": str(entity.type.value if hasattr(entity.type, "value") else entity.type),
            "slug": entity_slug(entity.id),
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

_recent_lock = threading.Lock()
_recent_cache: Dict[int, Tuple[float, List[Dict[str, Any]]]] = {}


def reset_recent_cache_for_testing() -> None:
    with _recent_lock:
        _recent_cache.clear()


@router.get("/recent-summaries")
def list_recent_summaries(
    request: Request,
    response: Response,
    limit: int = Query(6, ge=1, le=_RECENT_MAX),
    state: AppState = Depends(get_app_state),
) -> dict:
    """Newest public summaries across the corpus (the landing page feed)."""
    now = time.monotonic()
    with _recent_lock:
        cached = _recent_cache.get(limit)
    if cached and now - cached[0] <= _RECENT_TTL_SECONDS:
        items = cached[1]
    else:
        visibility = get_public_visibility(state)
        rows, _ = state.repository.list_public_episode_rows(limit=limit, order_by="published_at")
        items = _episode_items(state, visibility, rows, with_podcast=True)
        with _recent_lock:
            _recent_cache[limit] = (now, items)
    apply_public_cache_headers(request, response)
    return api_response({"episodes": items})


@router.get("/top-podcasts")
def list_public_top_podcasts(
    request: Request,
    response: Response,
    region: Optional[str] = Query(None, min_length=2, max_length=2),
    category: Optional[str] = Query(None, min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=_TOP_MAX_LIMIT),
    state: AppState = Depends(get_app_state),
) -> dict:
    """The chart for one region. Rows link to a show page only when the show is public."""
    visibility = get_public_visibility(state)
    available = state.repository.get_top_podcast_regions()
    resolved = _resolve_region(region, None, available)
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
    return api_response(
        {
            "region": resolved,
            "available_regions": available,
            "available_categories": state.repository.get_top_podcast_categories(resolved),
            "top_podcasts": items,
        }
    )
