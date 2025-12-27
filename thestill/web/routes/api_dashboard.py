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
Dashboard API endpoints for thestill.me web UI.

Provides statistics and recent activity for the dashboard.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...utils.duration import format_duration
from ..dependencies import AppState, get_app_state

router = APIRouter()


class DashboardStats(BaseModel):
    """Dashboard statistics response."""

    podcasts_tracked: int
    episodes_total: int
    episodes_processed: int
    episodes_pending: int
    storage_path: str
    audio_files_count: int
    transcripts_available: int
    pipeline: dict


class ActivityItem(BaseModel):
    """Recent activity item."""

    episode_id: str
    episode_title: str
    podcast_title: str
    action: str  # e.g., "summarized", "transcribed"
    timestamp: datetime


class ActivityResponse(BaseModel):
    """Recent activity response."""

    items: List[ActivityItem]
    count: int


@router.get("/stats")
async def get_dashboard_stats(state: AppState = Depends(get_app_state)) -> dict:
    """
    Get dashboard statistics.

    Returns:
        Statistics for the dashboard including podcast counts,
        episode processing states, and storage info.
    """
    stats = state.stats_service.get_stats()

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "podcasts_tracked": stats.podcasts_tracked,
        "episodes_total": stats.episodes_total,
        "episodes_processed": stats.episodes_summarized,
        "episodes_pending": stats.episodes_unprocessed,
        "storage_path": stats.storage_path,
        "audio_files_count": stats.audio_files_count,
        "transcripts_available": stats.transcripts_available,
        "pipeline": {
            "discovered": stats.episodes_discovered,
            "downloaded": stats.episodes_downloaded,
            "downsampled": stats.episodes_downsampled,
            "transcribed": stats.episodes_transcribed,
            "cleaned": stats.episodes_cleaned,
            "summarized": stats.episodes_summarized,
        },
    }


@router.get("/activity")
async def get_recent_activity(
    limit: int = 10,
    offset: int = 0,
    state: AppState = Depends(get_app_state),
) -> dict:
    """
    Get recent processing activity with pagination.

    Args:
        limit: Maximum number of activity items to return (default 10)
        offset: Number of items to skip for pagination (default 0)

    Returns:
        List of recently processed episodes with pagination info.
    """
    podcasts = state.repository.get_all()

    # Collect all episodes with their podcast info (all states)
    all_episodes = []
    for podcast in podcasts:
        for episode in podcast.episodes:
            all_episodes.append(
                {
                    "episode_id": episode.id,
                    "episode_title": episode.title,
                    "episode_slug": episode.slug,
                    "podcast_title": podcast.title,
                    "podcast_id": podcast.id,
                    "podcast_slug": podcast.slug,
                    "action": episode.state.value,  # discovered, downloaded, downsampled, transcribed, cleaned, summarized
                    "timestamp": episode.updated_at,
                    "pub_date": episode.pub_date,
                    "duration": episode.duration,
                    "duration_formatted": format_duration(episode.duration) if episode.duration else None,
                }
            )

    # Sort by updated_at descending
    all_episodes.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
    total = len(all_episodes)

    # Apply pagination
    items = all_episodes[offset : offset + limit]

    has_more = offset + len(items) < total
    next_offset = offset + limit if has_more else None

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "count": len(items),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": next_offset,
    }
