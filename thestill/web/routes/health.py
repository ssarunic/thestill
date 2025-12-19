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
Health check and status endpoints for thestill.me web server.

These endpoints are used for:
- Load balancer health checks
- Monitoring and alerting
- Quick status verification
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from ..dependencies import AppState, get_app_state

router = APIRouter()


@router.get("/")
async def root():
    """
    Root endpoint - basic service identification.

    Returns:
        Service name and status indicator.
    """
    return {
        "service": "thestill.me",
        "status": "ok",
    }


@router.get("/health")
async def health_check():
    """
    Health check endpoint for load balancers and monitoring.

    Returns:
        Health status with timestamp.
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status")
async def status(state: AppState = Depends(get_app_state)):
    """
    Detailed system status endpoint.

    Returns comprehensive statistics about the system,
    similar to the CLI 'status' command.

    Args:
        state: Application state with services

    Returns:
        System statistics and configuration info.
    """
    stats = state.stats_service.get_stats()

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "storage": {
            "path": str(stats.storage_path),
            "audio_files": stats.audio_files_count,
            "transcripts": stats.transcripts_available,
        },
        "podcasts": {
            "tracked": stats.podcasts_tracked,
            "total_episodes": stats.episodes_total,
        },
        "pipeline": {
            "discovered": stats.episodes_discovered,
            "downloaded": stats.episodes_downloaded,
            "downsampled": stats.episodes_downsampled,
            "transcribed": stats.episodes_transcribed,
            "cleaned": stats.episodes_cleaned,
            "summarized": stats.episodes_summarized,
            "unprocessed": stats.episodes_unprocessed,
        },
        "configuration": {
            "transcription_provider": state.config.transcription_provider,
            "llm_provider": state.config.llm_provider,
            "diarization_enabled": state.config.enable_diarization,
        },
    }
