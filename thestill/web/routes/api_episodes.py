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
Episodes API endpoints for thestill.me web UI.

Provides cross-podcast episode listing, search, and bulk operations.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...core.queue_manager import TaskStage
from ...models.podcast import EpisodeState
from ...utils.duration import format_duration
from ..dependencies import AppState, get_app_state

logger = logging.getLogger(__name__)

router = APIRouter()


# Request/Response models


class BulkProcessRequest(BaseModel):
    """Request body for bulk processing episodes."""

    episode_ids: List[str]


class BulkProcessTaskInfo(BaseModel):
    """Info about a queued task."""

    episode_id: str
    task_id: str
    stage: str


class BulkProcessResponse(BaseModel):
    """Response for bulk processing."""

    status: str
    queued: int
    skipped: int
    tasks: List[BulkProcessTaskInfo]


# Helper to map episode state to next pipeline stage
STATE_TO_NEXT_STAGE = {
    EpisodeState.DISCOVERED: TaskStage.DOWNLOAD,
    EpisodeState.DOWNLOADED: TaskStage.DOWNSAMPLE,
    EpisodeState.DOWNSAMPLED: TaskStage.TRANSCRIBE,
    EpisodeState.TRANSCRIBED: TaskStage.CLEAN,
    EpisodeState.CLEANED: TaskStage.SUMMARIZE,
    # SUMMARIZED has no next stage
}


@router.get("")
async def get_all_episodes(
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
    podcast_slug: Optional[str] = None,
    state: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort_by: str = "pub_date",
    sort_order: str = "desc",
    app_state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get episodes across all podcasts with filtering and pagination.

    Args:
        limit: Maximum number of episodes to return (default 20)
        offset: Number of episodes to skip for pagination (default 0)
        search: Case-insensitive title search (optional)
        podcast_slug: Filter by podcast slug (optional)
        state: Filter by processing state (optional)
        date_from: Only include episodes published on/after this date (ISO format)
        date_to: Only include episodes published on/before this date (ISO format)
        sort_by: Sort field - 'pub_date', 'title', or 'updated_at' (default 'pub_date')
        sort_order: Sort direction - 'asc' or 'desc' (default 'desc')

    Returns:
        List of episodes with their metadata, processing status, and pagination info.
    """
    # Resolve podcast_slug to podcast_id if provided
    podcast_id = None
    if podcast_slug:
        podcast = app_state.repository.get_by_slug(podcast_slug)
        if not podcast:
            raise HTTPException(status_code=404, detail=f"Podcast not found: {podcast_slug}")
        podcast_id = podcast.id

    # Parse date parameters
    parsed_date_from = None
    parsed_date_to = None
    if date_from:
        try:
            parsed_date_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date_from format: {date_from}")
    if date_to:
        try:
            parsed_date_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date_to format: {date_to}")

    # Query repository
    episodes_with_podcasts, total = app_state.repository.get_all_episodes(
        limit=limit,
        offset=offset,
        search=search,
        podcast_id=podcast_id,
        state=state,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    # Format response
    episodes = []
    for podcast, episode in episodes_with_podcasts:
        episodes.append(
            {
                "id": episode.id,
                "podcast_id": podcast.id,
                "podcast_slug": podcast.slug,
                "podcast_title": podcast.title,
                "podcast_image_url": podcast.image_url,
                "title": episode.title,
                "slug": episode.slug,
                "description": episode.description,
                "pub_date": episode.pub_date.isoformat() if episode.pub_date else None,
                "audio_url": str(episode.audio_url),
                "duration": episode.duration,
                "duration_formatted": format_duration(episode.duration) if episode.duration else None,
                "external_id": episode.external_id,
                "state": episode.state.value,
                "transcript_available": bool(episode.clean_transcript_path),
                "summary_available": bool(episode.summary_path),
            }
        )

    has_more = offset + len(episodes) < total
    next_offset = offset + limit if has_more else None

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "episodes": episodes,
        "count": len(episodes),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset,
    }


@router.post("/bulk/process")
async def bulk_process_episodes(
    request: BulkProcessRequest,
    app_state: AppState = Depends(get_app_state),
) -> BulkProcessResponse:
    """
    Queue next pipeline stage for multiple episodes.

    For each episode, determines the appropriate next stage based on current state
    and queues a task. Episodes that are already fully processed (summarized) are skipped.

    Args:
        request: List of episode IDs to process

    Returns:
        Summary of queued and skipped episodes, plus task details.
    """
    queued = 0
    skipped = 0
    tasks: List[BulkProcessTaskInfo] = []

    for episode_id in request.episode_ids:
        # Get episode and podcast
        result = app_state.repository.get_episode(episode_id)
        if not result:
            logger.warning(f"Episode not found for bulk processing: {episode_id}")
            skipped += 1
            continue

        podcast, episode = result

        # Determine next stage
        next_stage = STATE_TO_NEXT_STAGE.get(episode.state)
        if not next_stage:
            # Already summarized or unknown state
            skipped += 1
            continue

        # Queue the task
        try:
            task = app_state.queue_manager.add_task(episode_id, next_stage)
            tasks.append(
                BulkProcessTaskInfo(
                    episode_id=episode_id,
                    task_id=task.id,
                    stage=next_stage.value,
                )
            )
            queued += 1
        except Exception as e:
            logger.error(f"Failed to queue task for episode {episode_id}: {e}")
            skipped += 1

    return BulkProcessResponse(
        status="ok",
        queued=queued,
        skipped=skipped,
        tasks=tasks,
    )
