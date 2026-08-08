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
System status API endpoint for Thestill web server.

Provides detailed system status and statistics, similar to the CLI 'status' command.
"""

from fastapi import APIRouter, Depends, Request

from ..dependencies import AppState, get_app_state
from ..responses import api_response

router = APIRouter()


@router.get("/mcp")
def get_mcp_status(request: Request, state: AppState = Depends(get_app_state)):
    """Remote MCP connector info (spec #71 Phase 1).

    Admin-gated at the router mount (this router carries ``require_admin``
    in ``app.py``) because the returned capability URL is
    operator-equivalent access. The Settings page renders it with a copy
    button; when the feature is off the response says so and the UI shows
    the env vars needed to enable it.
    """
    config = state.config
    if not config.mcp_http_enabled or not config.mcp_http_secret:
        return api_response({"mcp": {"enabled": False}})

    from ..mcp_http import connector_url

    return api_response(
        {
            "mcp": {
                "enabled": True,
                "url": connector_url(config, str(request.base_url)),
                "transport": "streamable-http",
            }
        }
    )


@router.get("")
def get_status(state: AppState = Depends(get_app_state)):
    """
    Get detailed system status.

    Returns comprehensive statistics about the system,
    similar to the CLI 'status' command.

    Args:
        state: Application state with services

    Returns:
        System statistics and configuration info.
    """
    stats = state.stats_service.get_stats()

    return api_response(
        {
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
            # Spec #60 — feed refresh health (parked/quarantined by reason).
            "refresh_health": {
                "active": stats.refresh_active,
                "due_now": stats.refresh_due_now,
                "backing_off": stats.refresh_backing_off,
                "parked_total": stats.refresh_parked_total,
                "parked_by_reason": stats.refresh_parked_by_reason,
            },
        }
    )
