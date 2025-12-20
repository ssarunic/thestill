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
Episode API endpoints for thestill.me web UI.

Provides read-only access to episode content (transcripts, summaries).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import AppState, get_app_state

router = APIRouter()


@router.get("/{episode_id}")
async def get_episode(
    episode_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get a specific episode by ID.

    Args:
        episode_id: Episode UUID

    Returns:
        Episode details with metadata.
    """
    # Look up the episode directly from repository
    result = state.repository.get_episode(episode_id)

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


@router.get("/{episode_id}/transcript")
async def get_episode_transcript(
    episode_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get the cleaned transcript for an episode.

    Args:
        episode_id: Episode UUID

    Returns:
        Cleaned Markdown transcript content.
    """
    # Look up the episode
    result = state.repository.get_episode(episode_id)

    if not result:
        raise HTTPException(status_code=404, detail="Episode not found")

    podcast, episode = result

    # Get transcript using the service
    transcript = state.podcast_service.get_transcript(podcast.id, episode.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episode_id": episode_id,
        "episode_title": episode.title,
        "content": transcript,
        "available": not transcript.startswith("N/A"),
    }


@router.get("/{episode_id}/summary")
async def get_episode_summary(
    episode_id: str,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get the summary for an episode.

    Args:
        episode_id: Episode UUID

    Returns:
        Summary Markdown content.
    """
    # Look up the episode
    result = state.repository.get_episode(episode_id)

    if not result:
        raise HTTPException(status_code=404, detail="Episode not found")

    podcast, episode = result

    # Get summary using the service
    summary = state.podcast_service.get_summary(podcast.id, episode.id)

    if summary is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episode_id": episode_id,
        "episode_title": episode.title,
        "content": summary,
        "available": not summary.startswith("N/A"),
    }
