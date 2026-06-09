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

import threading
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from structlog import get_logger

from ...models.user import User
from ...services.follower_service import AlreadyFollowingError, NotFollowingError, PodcastNotFoundError
from ...utils.duration import format_duration
from ..dependencies import AppState, get_app_state, get_current_user, require_auth
from ..responses import api_response, conflict, not_found, paginated_response

logger = get_logger(__name__)

router = APIRouter()


class ResolvePodcastRequest(BaseModel):
    """Request body for the resolve-podcast endpoint."""

    url: str


@router.post("/resolve")
async def resolve_podcast(
    request: ResolvePodcastRequest,
    state: AppState = Depends(get_app_state),
    _: User = Depends(require_auth),
) -> dict:
    """
    Resolve a podcast URL to a local slug, creating the row if needed.

    This is the "lazy import" path used by the Top Podcasts list when a user
    clicks a chart entry that hasn't been imported yet. The synchronous part
    only fetches RSS metadata and persists the ``podcasts`` row (~1–2s); the
    full episode discovery runs in a background daemon thread so the caller
    can navigate to the detail page immediately and watch episodes fill in.

    Unlike ``POST /api/commands/add``, this endpoint:
      - does NOT auto-follow the podcast for the caller
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
    # "new vs existing" from the URL alone. Use ``last_processed`` instead:
    # it's ``None`` until the first refresh completes, so it's True for both
    # genuinely-new rows and stale rows that never finished discovery — both
    # cases want a background refresh.
    try:
        podcast = state.podcast_service.add_podcast(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if podcast is None:
        raise HTTPException(
            status_code=400,
            detail="Failed to resolve podcast — the URL may be invalid or unreachable.",
        )

    is_new = podcast.last_processed is None

    if is_new:
        max_episodes_per_podcast = state.config.max_episodes_per_podcast

        def _background_refresh(podcast_id: str) -> None:
            try:
                state.refresh_service.refresh(
                    podcast_id=podcast_id,
                    max_episodes_per_podcast=max_episodes_per_podcast,
                )
            except Exception:
                logger.exception("resolve_podcast_background_refresh_failed", podcast_id=podcast_id)

        threading.Thread(target=_background_refresh, args=(str(podcast.id),), daemon=True).start()

    logger.info(
        "podcast_resolved",
        podcast_id=podcast.id,
        podcast_slug=podcast.slug,
        is_new=is_new,
    )

    return api_response(
        {
            "podcast_slug": podcast.slug,
            "podcast_id": podcast.id,
            "is_new": is_new,
        }
    )


@router.get("")
async def get_podcasts(
    limit: int = 12,
    offset: int = 0,
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

    Returns:
        List of followed podcasts with their metadata, episode counts, and pagination info.
    """
    # Get IDs of podcasts the user follows
    followed_podcast_ids = set(state.follower_repository.get_followed_podcast_ids(user.id))

    # Get all podcasts with their indexed info
    all_podcasts = state.podcast_service.get_podcasts()

    # Filter to only followed podcasts
    followed_podcasts = [p for p in all_podcasts if p.id in followed_podcast_ids]
    total = len(followed_podcasts)

    # Apply pagination
    podcasts = followed_podcasts[offset : offset + limit]

    # Add is_following flag (always true for this endpoint)
    podcast_dicts = []
    for p in podcasts:
        podcast_dict = p.model_dump()
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
async def get_podcast(
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
    podcast = state.repository.get_by_slug(podcast_slug)

    if not podcast:
        not_found("Podcast", podcast_slug)

    # Get the indexed version for extra info
    podcasts = state.podcast_service.get_podcasts()
    podcast_info = next((p for p in podcasts if str(p.rss_url) == str(podcast.rss_url)), None)

    is_following = bool(user) and state.follower_repository.exists(user.id, podcast.id)

    return api_response(
        {
            "podcast": {
                "id": podcast.id,
                "index": podcast_info.index if podcast_info else 0,
                "title": podcast.title,
                "description": podcast.description,
                "rss_url": str(podcast.rss_url),
                "slug": podcast.slug,
                "image_url": podcast.image_url,
                "primary_category": podcast.primary_category,
                "primary_subcategory": podcast.primary_subcategory,
                "secondary_category": podcast.secondary_category,
                "secondary_subcategory": podcast.secondary_subcategory,
                # ``last_processed`` is the discovery watermark (newest episode
                # pub_date); ``last_processed_at`` is the wall-clock processing
                # time the UI's "last processed" indicator should use.
                "last_processed": podcast.last_processed.isoformat() if podcast.last_processed else None,
                "last_processed_at": podcast.last_processed_at.isoformat() if podcast.last_processed_at else None,
                "episodes_count": len(podcast.episodes),
                "episodes_processed": podcast_info.episodes_processed if podcast_info else 0,
                "is_following": is_following,
                # THES-146: New metadata fields
                "author": podcast.author,
                "explicit": podcast.explicit,
                "show_type": podcast.show_type,
                "website_url": podcast.website_url,
                "is_complete": podcast.is_complete,
                "copyright": podcast.copyright,
            },
        }
    )


@router.get("/{podcast_slug}/episodes")
async def get_podcast_episodes(
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
async def get_episode_by_slugs(
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
async def get_episode_transcript_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
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

    return api_response(response_payload)


@router.get("/{podcast_slug}/episodes/{episode_slug}/summary")
async def get_episode_summary_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
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

    summary = state.podcast_service.get_summary(podcast.id, episode.id)

    if summary is None:
        not_found("Episode", f"{podcast_slug}/{episode_slug}")

    return api_response(
        {
            "episode_id": episode.id,
            "episode_title": episode.title,
            "content": summary,
            "available": not summary.startswith("N/A"),
        }
    )


# =============================================================================
# Follow/Unfollow Endpoints
# =============================================================================


@router.post("/{podcast_slug}/follow", status_code=201)
async def follow_podcast(
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
async def unfollow_podcast(
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
async def get_podcast_follower_count(
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
