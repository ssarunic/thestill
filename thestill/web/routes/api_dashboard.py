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
Dashboard API endpoints for Thestill web UI.

Provides statistics and recent activity for the dashboard.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from structlog import get_logger

from ...services.narration import read_narration_header
from ...utils.duration import format_duration
from ..dependencies import AppState, get_app_state
from ..responses import api_response, paginated_response

logger = get_logger(__name__)

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

    return api_response(
        {
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
    )


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
                    "episode_image_url": episode.image_url,
                    "podcast_image_url": podcast.image_url,
                }
            )

    # Sort by updated_at descending
    all_episodes.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
    total = len(all_episodes)

    # Apply pagination
    items = all_episodes[offset : offset + limit]

    return paginated_response(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        items_key="items",
    )


@router.get("/narration")
async def get_narration_dashboard(state: AppState = Depends(get_app_state)) -> dict:
    """Aggregate narration runs for the dashboard tile (spec #33 Phase 5).

    Filesystem-driven so no schema migration: the runner writes one
    JSON header per variant under ``data/narrations/``. We read every
    one and roll up the metrics that matter for the operator: total
    runs, fallback rate, average actual/target runtime, average
    latency (when captured), and a pointer to the latest run so the
    tile can deep-link into the digest viewer.
    """
    narrations_dir = state.path_manager.narrations_dir()
    runs: List[dict] = []
    if narrations_dir.exists():
        for json_path in sorted(narrations_dir.glob("*.json")):
            header = read_narration_header(json_path)
            if header is None:
                continue
            runs.append({"path": json_path, "header": header})

    total = len(runs)
    fallback_count = sum(1 for r in runs if r["header"].get("mode") == "fallback")
    fallback_rate = (fallback_count / total) if total > 0 else 0.0

    actual_seconds_values = [
        float(r["header"]["actual_duration_seconds"])
        for r in runs
        if isinstance(r["header"].get("actual_duration_seconds"), (int, float))
    ]
    target_seconds_values = [
        int(r["header"]["target_duration_seconds"])
        for r in runs
        if isinstance(r["header"].get("target_duration_seconds"), int)
    ]
    latency_values = [
        int(r["header"]["latency_ms"])
        for r in runs
        if isinstance(r["header"].get("latency_ms"), int)
    ]

    avg_actual = (sum(actual_seconds_values) / len(actual_seconds_values)) if actual_seconds_values else None
    avg_target = (sum(target_seconds_values) / len(target_seconds_values)) if target_seconds_values else None
    avg_latency_ms = (sum(latency_values) / len(latency_values)) if latency_values else None

    latest = None
    if runs:
        latest_run = max(
            runs,
            key=lambda r: r["header"].get("generated_at") or "",
        )
        header = latest_run["header"]
        # ``digest_id`` is persisted in the JSON header by the runner
        # so consumers don't have to parse the filename. Slugs may
        # contain ``-`` (e.g. ``custom-450s``) so the filename alone
        # is ambiguous; older artefacts written before this field
        # was added surface ``None`` and the tile hides its deep-link.
        latest = {
            "narration_id": latest_run["path"].stem,
            "digest_id": header.get("digest_id"),
            "generated_at": header.get("generated_at"),
            "mode": header.get("mode"),
            "fallback_reason": header.get("fallback_reason"),
            "target_duration_seconds": header.get("target_duration_seconds"),
            "actual_duration_seconds": header.get("actual_duration_seconds"),
            "latency_ms": header.get("latency_ms"),
        }

    return api_response(
        {
            "total_runs": total,
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_rate, 4),
            "avg_actual_duration_seconds": round(avg_actual, 2) if avg_actual is not None else None,
            "avg_target_duration_seconds": round(avg_target, 2) if avg_target is not None else None,
            "avg_latency_ms": int(avg_latency_ms) if avg_latency_ms is not None else None,
            "latest": latest,
        }
    )
