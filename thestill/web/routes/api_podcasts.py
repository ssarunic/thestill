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

"""
Podcast API endpoints for Thestill web UI.

Provides access to podcasts, episodes, and follow/unfollow functionality.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from structlog import get_logger

from ...models.user import User
from ...services.follower_service import AlreadyFollowingError, NotFollowingError, PodcastNotFoundError
from ...services.playback import build_playback_manifest
from ...services.podcast_add import add_podcast_and_auto_follow
from ...services.podcast_service import PodcastWithIndex
from ...utils.duration import format_duration
from ...utils.language_config import normalize_language_code
from ..dependencies import AppState, get_app_state, get_current_user, require_auth
from ..responses import api_response, conflict, etag_json_response, not_found, paginated_response

logger = get_logger(__name__)

router = APIRouter()


class ResolvePodcastRequest(BaseModel):
    """Request body for the resolve-podcast endpoint."""

    url: str


@router.post("/resolve")
def resolve_podcast(
    request: ResolvePodcastRequest,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Resolve a podcast URL to a local slug, creating the row if needed.

    This is the "lazy import" path used by the Top Podcasts list when a user
    clicks a chart entry that hasn't been imported yet. The synchronous part
    only fetches RSS metadata and persists the ``podcasts`` row (~1–2s); the
    episode discovery is a ``REFRESH_FEED`` task enqueued through the
    refresh-on-open service (spec #74) so the caller can navigate to the
    detail page immediately and watch episodes fill in. A queue row — not a
    daemon thread — means the first discovery is observable
    (``refresh_pending``), retried on failure, and re-triggerable by a later
    open if it never completed.

    Unlike ``POST /api/commands/add``, this endpoint:
      - does NOT auto-follow the podcast for the caller in multi-user
        mode (in single-user mode the default user is auto-followed —
        spec #63, or the universal follower gate would never refresh it)
      - does NOT use the single-instance task manager, so multiple resolves
        can run in parallel (FastAPI runs sync defs in a threadpool)
      - returns the slug synchronously, never a job ID

    The operation is idempotent: calling it with a URL that already maps to
    an existing podcast simply returns the existing slug (``is_new=False``)
    without re-fetching the feed.
    """
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    # ``podcast_service.add_podcast`` is idempotent and resolves Apple/YouTube
    # URLs to a canonical RSS URL before checking existence, so we can't tell
    # "new vs existing" from the URL alone. ``is_new`` (informational) keys
    # off ``last_processed``, which stays ``None`` until a refresh has
    # persisted a dated episode; the refresh decision itself is the
    # service's (queue state + cooldowns), never this watermark.
    try:
        podcast = add_podcast_and_auto_follow(
            state.podcast_service,
            state.follower_service,
            state.auth_service,
            state.config,
            url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if podcast is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to resolve podcast — the URL may be invalid or unreachable.",
        )

    is_new = podcast.last_processed is None

    refresh_pending = False
    if state.refresh_on_open is not None:
        refresh_pending = state.refresh_on_open.maybe_trigger(str(podcast.id)).pending
    else:  # hand-built fixtures only; production wiring always passes the service
        logger.warning("resolve_podcast_refresh_service_missing", podcast_id=podcast.id)

    logger.info(
        "podcast_resolved",
        podcast_id=podcast.id,
        podcast_slug=podcast.slug,
        is_new=is_new,
        refresh_pending=refresh_pending,
    )

    return api_response(
        {
            "podcast_slug": podcast.slug,
            "podcast_id": podcast.id,
            "is_new": is_new,
            "refresh_pending": refresh_pending,
        }
    )


@router.get("")
def get_podcasts(
    limit: int = 12,
    offset: int = 0,
    q: Optional[str] = None,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """
    Get podcasts the current user follows with pagination.

    Returns only podcasts that the authenticated user is following.
    Requires authentication.

    Args:
        limit: Maximum number of podcasts to return (default 12)
        offset: Number of podcasts to skip for pagination (default 0)
        q: Optional case-insensitive filter on podcast title or author

    Returns:
        List of followed podcasts with their metadata, episode counts, and pagination info.
    """
    # Get IDs of podcasts the user follows
    followed_podcast_ids = state.follower_repository.get_followed_podcast_ids(user.id)

    # Spec #69 Phase 4 — filter/search/paginate in SQL instead of hydrating
    # the whole corpus (every episode of every podcast) per page view. The
    # filter runs before pagination so `total` reflects the filtered set
    # and infinite scroll pages stay consistent with the query.
    rows, total = state.repository.list_podcast_rows(podcast_ids=followed_podcast_ids, q=q, limit=limit, offset=offset)

    # Add is_following flag (always true for this endpoint). ``index`` was
    # only ever consumed as a stable list key — page position serves that.
    podcast_dicts = []
    for i, row in enumerate(rows):
        podcast_dict = PodcastWithIndex(index=offset + i + 1, **row).model_dump()
        podcast_dict["is_following"] = True
        podcast_dicts.append(podcast_dict)

    return paginated_response(
        items=podcast_dicts,
        total=total,
        offset=offset,
        limit=limit,
        items_key="podcasts",
    )


@router.get("/{podcast_slug}")
def get_podcast(
    podcast_slug: str,
    state: AppState = Depends(get_app_state),
    user: Optional[User] = Depends(get_current_user),
) -> dict:
    """
    Get a specific podcast by slug.

    Args:
        podcast_slug: URL-safe podcast identifier

    Returns:
        Podcast details with episode count.
    """
    # Spec #69 Phase 4 — metadata + counts in one light query; previously
    # this hydrated the podcast's whole back catalog AND rescanned the full
    # corpus just to recover episode counts. ``index`` (a legacy list
    # position nothing renders on this page) is pinned to 0.
    row = state.repository.get_podcast_row_by_slug(podcast_slug)

    if not row:
        not_found("Podcast", podcast_slug)

    is_following = bool(user) and state.follower_repository.exists(user.id, row["id"])

    # Spec #74 — an open is a demand signal the follower gate can't see.
    # Enqueue at most one REFRESH_FEED for this feed (coalesced with the
    # lazy-import discovery and scheduler tasks; throttled by the feed's
    # cooldowns). ``refresh_pending`` tells the UI to poll until it lands.
    refresh_pending = False
    if state.refresh_on_open is not None:
        refresh_pending = state.refresh_on_open.maybe_trigger(row["id"]).pending

    # PodcastWithIndex normalizes the row (ISO-string datetimes from the
    # SQLite backend become datetime objects) so the response shape is
    # backend-independent.
    info = PodcastWithIndex(index=0, **row)

    return api_response(
        {
            "podcast": {
                "id": info.id,
                "index": info.index,
                "title": info.title,
                "description": info.description,
                "rss_url": info.rss_url,
                "slug": info.slug,
                "image_url": info.image_url,
                "primary_category": info.primary_category,
                "primary_subcategory": info.primary_subcategory,
                "secondary_category": info.secondary_category,
                "secondary_subcategory": info.secondary_subcategory,
                # ``last_processed`` is the discovery watermark (newest episode
                # pub_date); ``last_processed_at`` is the wall-clock processing
                # time the UI's "last processed" indicator should use.
                "last_processed": info.last_processed.isoformat() if info.last_processed else None,
                "last_processed_at": info.last_processed_at.isoformat() if info.last_processed_at else None,
                "episodes_count": info.episodes_count,
                "episodes_processed": info.episodes_processed,
                "is_following": is_following,
                # Spec #74 — True while an open-triggered (or any) REFRESH_FEED
                # for this podcast is queued or running.
                "refresh_pending": refresh_pending,
                # THES-146: New metadata fields
                "author": info.author,
                "explicit": info.explicit,
                "show_type": info.show_type,
                "website_url": info.website_url,
                "is_complete": info.is_complete,
                "copyright": info.copyright,
            },
        }
    )


@router.get("/{podcast_slug}/episodes")
def get_podcast_episodes(
    podcast_slug: str,
    limit: int = 20,
    offset: int = 0,
    since_hours: Optional[int] = None,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get episodes for a specific podcast with pagination.

    Args:
        podcast_slug: URL-safe podcast identifier
        limit: Maximum number of episodes to return (default 20)
        offset: Number of episodes to skip for pagination (default 0)
        since_hours: Only include episodes published in last N hours

    Returns:
        List of episodes with their metadata, processing status, and pagination info.
    """
    podcast = state.repository.get_by_slug(podcast_slug)
    if not podcast:
        not_found("Podcast", podcast_slug)

    # Get total count for pagination
    total = state.podcast_service.get_episodes_count(podcast.id, since_hours=since_hours)
    if total is None:
        not_found("Podcast", podcast_slug)

    episodes = state.podcast_service.get_episodes(podcast.id, limit=limit, offset=offset, since_hours=since_hours)

    if episodes is None:
        not_found("Podcast", podcast_slug)

    return paginated_response(
        items=[e.model_dump() for e in episodes],
        total=total,
        offset=offset,
        limit=limit,
        items_key="episodes",
    )


@router.get("/{podcast_slug}/episodes/{episode_slug}")
def get_episode_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get a specific episode by podcast slug and episode slug.

    Args:
        podcast_slug: URL-safe podcast identifier
        episode_slug: URL-safe episode identifier

    Returns:
        Episode details with metadata.
    """
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)

    if not result:
        not_found("Episode", f"{podcast_slug}/{episode_slug}")

    podcast, episode = result
    alternate_enclosures = state.repository.get_alternate_enclosures(episode.id)

    return api_response(
        {
            "episode": {
                "id": episode.id,
                "podcast_id": podcast.id,
                "podcast_slug": podcast.slug,
                "podcast_title": podcast.title,
                "title": episode.title,
                "description": episode.description,
                "description_html": episode.description_html,
                "slug": episode.slug,
                "pub_date": episode.pub_date.isoformat() if episode.pub_date else None,
                "audio_url": str(episode.audio_url),
                # Spec #61 §4 — playback-asset manifest. Audio-only episodes
                # emit kind 'audio' with the existing URL (no client break);
                # RSS video-enclosure episodes emit a video asset + poster;
                # video/youtube alternate enclosures add the opt-in youtube
                # asset (spec #62).
                "playback": build_playback_manifest(episode, alternate_enclosures),
                "duration": episode.duration,
                "duration_formatted": format_duration(episode.duration) if episode.duration else None,
                "external_id": episode.external_id,
                "state": episode.state.value,
                "has_transcript": bool(episode.clean_transcript_path),
                "has_summary": bool(episode.summary_path),
                "image_url": episode.image_url,
                "podcast_image_url": podcast.image_url,
                # THES-146: New metadata fields
                "explicit": episode.explicit,
                "episode_type": episode.episode_type,
                "episode_number": episode.episode_number,
                "season_number": episode.season_number,
                "website_url": episode.website_url,
                # Failure info
                "is_failed": episode.is_failed,
                "failed_at_stage": episode.failed_at_stage,
                "failure_reason": episode.failure_reason,
                "failure_type": episode.failure_type.value if episode.failure_type else None,
                "failed_at": episode.failed_at.isoformat() if episode.failed_at else None,
            },
        }
    )


@router.get("/{podcast_slug}/episodes/{episode_slug}/transcript")
def get_episode_transcript_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    request: Request,
    state: AppState = Depends(get_app_state),
) -> Response:
    """
    Get the transcript for an episode by slugs.

    Returns cleaned transcript if available, otherwise falls back to raw transcript.

    Args:
        podcast_slug: URL-safe podcast identifier
        episode_slug: URL-safe episode identifier

    Returns:
        Markdown transcript content with type indicator ("cleaned" or "raw").
    """
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)

    if not result:
        not_found("Episode", f"{podcast_slug}/{episode_slug}")

    podcast, episode = result

    # ``episode`` is already resolved above via repository.get_episode_by_slug.
    # Use the ``_for_episode`` service methods so the three transcript
    # fetches don't each re-walk the podcast/episode lookup.
    transcript_result = state.podcast_service.get_transcript_for_episode(episode)

    response_payload: dict = {
        "episode_id": episode.id,
        "episode_title": episode.title,
        "content": transcript_result.content,
        "available": transcript_result.transcript_type is not None,
        "transcript_type": transcript_result.transcript_type,
    }

    segmented = state.podcast_service.get_segmented_transcript_for_episode(episode)
    if segmented is not None:
        response_payload["segments"] = segmented.annotated.model_dump()
        # Spec #69 Phase 6.3 — don't double-ship the transcript: when the
        # segmented structure is present the reader renders from it and
        # never reads ``content`` (the markdown is the segments' text again,
        # so shipping both ~doubles the payload). ``content`` stays a string
        # for the wire contract; the fallback viewer only mounts when
        # ``segments`` is absent.
        response_payload["content"] = ""

    # Write-once resource: content-hash ETag + revalidation (Phase 6.2).
    return etag_json_response(request, response_payload)


@router.get("/{podcast_slug}/episodes/{episode_slug}/summary")
def get_episode_summary_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    request: Request,
    lang: Optional[str] = Query(None, pattern=r"^[A-Za-z]{2,3}$"),
    state: AppState = Depends(get_app_state),
) -> Response:
    """
    Get the summary for an episode by slugs.

    Args:
        podcast_slug: URL-safe podcast identifier
        episode_slug: URL-safe episode identifier

    Returns:
        Summary Markdown content.
    """
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)

    if not result:
        not_found("Episode", f"{podcast_slug}/{episode_slug}")

    podcast, episode = result

    podcast_language = normalize_language_code(podcast.language)
    provider = None
    canonical_language = state.podcast_service.get_recorded_summary_language(episode)
    if canonical_language is None and episode.summary_path:
        try:
            from ...core.llm_provider import create_llm_provider_from_config

            provider = create_llm_provider_from_config(state.config)
            canonical_language = state.podcast_service.detect_and_record_summary_language(
                episode,
                podcast_language=podcast_language,
                provider=provider,
            )
        except Exception as exc:  # Legacy invariant is the safe no-provider fallback.
            logger.warning(
                "summary_language.detect_failed",
                episode_id=episode.id,
                error=str(exc),
            )
            canonical_language = "en"
    canonical_language = normalize_language_code(canonical_language, default=podcast_language)
    requested_language = normalize_language_code(lang, default=canonical_language) if lang else canonical_language
    summary = state.podcast_service.get_summary_for_episode(
        episode,
        language=requested_language,
        canonical_language=canonical_language,
    )

    # A missing non-canonical variant is generated synchronously on first
    # request, then served from FileStorage on subsequent requests.
    if summary is None and requested_language != canonical_language and episode.summary_path:
        if provider is None:
            from ...core.llm_provider import create_llm_provider_from_config

            provider = create_llm_provider_from_config(state.config)
        summary = state.podcast_service.get_or_create_summary_translation(
            episode,
            source_language=canonical_language,
            target_language=requested_language,
            provider=provider,
        )

    if summary is None:
        raise HTTPException(status_code=500, detail="Summary translation could not be generated")

    citations = None
    if not summary.startswith("N/A"):
        citations = state.podcast_service.get_summary_citations_for_episode(
            episode,
            summary,
            language=requested_language,
            canonical_language=canonical_language,
        )

    available_languages = state.podcast_service.get_available_summary_languages(
        episode,
        canonical_language=canonical_language,
    )

    # Write-once (per language) resource: content-hash ETag (Phase 6.2).
    return etag_json_response(
        request,
        {
            "episode_id": episode.id,
            "episode_title": episode.title,
            "content": summary,
            "available": not summary.startswith("N/A"),
            "citations": citations,
            "language": requested_language,
            "podcast_language": podcast_language,
            "canonical_language": canonical_language,
            "available_languages": available_languages,
        },
    )


# =============================================================================
# Follow/Unfollow Endpoints
# =============================================================================


@router.post("/{podcast_slug}/follow", status_code=201)
def follow_podcast(
    podcast_slug: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """
    Follow a podcast.

    Creates a follow relationship between the authenticated user and the podcast.
    Requires authentication.

    Args:
        podcast_slug: URL-safe podcast identifier

    Returns:
        Success response with follow details.

    Raises:
        404: Podcast not found
        409: Already following this podcast
    """
    try:
        follower = state.follower_service.follow_by_slug(user.id, podcast_slug)
        return api_response(
            {
                "message": "Successfully followed podcast",
                "podcast_slug": podcast_slug,
                "followed_at": follower.created_at.isoformat(),
            }
        )
    except PodcastNotFoundError:
        not_found("Podcast", podcast_slug)
    except AlreadyFollowingError:
        conflict(f"Already following podcast: {podcast_slug}")


@router.delete("/{podcast_slug}/follow", status_code=204)
def unfollow_podcast(
    podcast_slug: str,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> Response:
    """
    Unfollow a podcast.

    Removes the follow relationship between the authenticated user and the podcast.
    The podcast remains in the system (can be re-followed via URL).
    Requires authentication.

    Args:
        podcast_slug: URL-safe podcast identifier

    Returns:
        204 No Content on success.

    Raises:
        404: Podcast not found or not following
    """
    try:
        state.follower_service.unfollow_by_slug(user.id, podcast_slug)
        return Response(status_code=204)
    except PodcastNotFoundError:
        not_found("Podcast", podcast_slug)
    except NotFollowingError:
        not_found("Follow relationship", podcast_slug)


@router.get("/{podcast_slug}/followers/count")
def get_podcast_follower_count(
    podcast_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get the number of followers for a podcast.

    Args:
        podcast_slug: URL-safe podcast identifier

    Returns:
        Follower count for the podcast.

    Raises:
        404: Podcast not found
    """
    count = state.follower_service.get_follower_count_by_slug(podcast_slug)

    if count is None:
        not_found("Podcast", podcast_slug)

    return api_response(
        {
            "podcast_slug": podcast_slug,
            "follower_count": count,
        }
    )
