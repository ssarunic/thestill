# Copyright 2025 thestill.me
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
Podcast API endpoints for thestill.me web UI.

Provides read-only access to podcasts and their episodes.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import AppState, get_app_state

router = APIRouter()


@router.get("")
async def get_podcasts(
    limit: int = 12,
    offset: int = 0,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get tracked podcasts with pagination.

    Args:
        limit: Maximum number of podcasts to return (default 12)
        offset: Number of podcasts to skip for pagination (default 0)

    Returns:
        List of podcasts with their metadata, episode counts, and pagination info.
    """
    all_podcasts = state.podcast_service.get_podcasts()
    total = len(all_podcasts)

    # Apply pagination
    podcasts = all_podcasts[offset : offset + limit]

    has_more = offset + len(podcasts) < total
    next_offset = offset + limit if has_more else None

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "podcasts": [p.model_dump() for p in podcasts],
        "count": len(podcasts),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset,
    }


@router.get("/{podcast_id}")
async def get_podcast(
    podcast_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get a specific podcast by ID.

    Args:
        podcast_id: Podcast index (1-based), UUID, or RSS URL

    Returns:
        Podcast details with episode count.
    """
    # Try to parse as int for index-based lookup
    try:
        pid = int(podcast_id)
    except ValueError:
        pid = podcast_id

    podcast = state.podcast_service.get_podcast(pid)

    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")

    # Get the indexed version for extra info
    podcasts = state.podcast_service.get_podcasts()
    podcast_info = next((p for p in podcasts if str(p.rss_url) == str(podcast.rss_url)), None)

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "podcast": {
            "id": podcast.id,
            "index": podcast_info.index if podcast_info else 0,
            "title": podcast.title,
            "description": podcast.description,
            "rss_url": str(podcast.rss_url),
            "slug": podcast.slug,
            "image_url": podcast.image_url,
            "last_processed": podcast.last_processed.isoformat() if podcast.last_processed else None,
            "episodes_count": len(podcast.episodes),
            "episodes_processed": podcast_info.episodes_processed if podcast_info else 0,
        },
    }


@router.get("/{podcast_id}/episodes")
async def get_podcast_episodes(
    podcast_id: str,
    limit: int = 20,
    offset: int = 0,
    since_hours: Optional[int] = None,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get episodes for a specific podcast with pagination.

    Args:
        podcast_id: Podcast index (1-based), UUID, or RSS URL
        limit: Maximum number of episodes to return (default 20)
        offset: Number of episodes to skip for pagination (default 0)
        since_hours: Only include episodes published in last N hours

    Returns:
        List of episodes with their metadata, processing status, and pagination info.
    """
    # Try to parse as int for index-based lookup
    try:
        pid = int(podcast_id)
    except ValueError:
        pid = podcast_id

    # Get total count for pagination
    total = state.podcast_service.get_episodes_count(pid, since_hours=since_hours)
    if total is None:
        raise HTTPException(status_code=404, detail="Podcast not found")

    episodes = state.podcast_service.get_episodes(pid, limit=limit, offset=offset, since_hours=since_hours)

    if episodes is None:
        raise HTTPException(status_code=404, detail="Podcast not found")

    has_more = offset + len(episodes) < total
    next_offset = offset + limit if has_more else None

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episodes": [e.model_dump() for e in episodes],
        "count": len(episodes),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset,
    }


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
        raise HTTPException(status_code=404, detail="Episode not found")

    podcast, episode = result

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episode": {
            "id": episode.id,
            "podcast_id": podcast.id,
            "podcast_slug": podcast.slug,
            "podcast_title": podcast.title,
            "title": episode.title,
            "description": episode.description,
            "slug": episode.slug,
            "pub_date": episode.pub_date.isoformat() if episode.pub_date else None,
            "audio_url": str(episode.audio_url),
            "duration": episode.duration,
            "external_id": episode.external_id,
            "state": episode.state.value,
            "has_transcript": bool(episode.clean_transcript_path),
            "has_summary": bool(episode.summary_path),
        },
    }


@router.get("/{podcast_slug}/episodes/{episode_slug}/transcript")
async def get_episode_transcript_by_slugs(
    podcast_slug: str,
    episode_slug: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get the cleaned transcript for an episode by slugs.

    Args:
        podcast_slug: URL-safe podcast identifier
        episode_slug: URL-safe episode identifier

    Returns:
        Cleaned Markdown transcript content.
    """
    result = state.repository.get_episode_by_slug(podcast_slug, episode_slug)

    if not result:
        raise HTTPException(status_code=404, detail="Episode not found")

    podcast, episode = result

    transcript = state.podcast_service.get_transcript(podcast.id, episode.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episode_id": episode.id,
        "episode_title": episode.title,
        "content": transcript,
        "available": not transcript.startswith("N/A"),
    }


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
        raise HTTPException(status_code=404, detail="Episode not found")

    podcast, episode = result

    summary = state.podcast_service.get_summary(podcast.id, episode.id)

    if summary is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episode_id": episode.id,
        "episode_title": episode.title,
        "content": summary,
        "available": not summary.startswith("N/A"),
    }
