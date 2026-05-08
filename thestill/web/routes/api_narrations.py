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

"""Narrated digest API endpoints (spec #33 Phase 3).

Returns 503 when the runner is unavailable (``narration_enabled`` is
False or the LLM provider failed to initialise) so callers can handle
the rollout gate cleanly.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from structlog import get_logger

from ...services.narration import NarrationRunnerError
from ...utils.duration import parse_target_duration
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request, not_found

logger = get_logger(__name__)

router = APIRouter()


class CreateNarrationRequest(BaseModel):
    """Body for ``POST /api/narrations`` (spec §"CLI & API")."""

    digest_id: Optional[str] = Field(
        default=None,
        description="Digest id to narrate. Omit to use the latest digest.",
    )
    target_duration_seconds: Optional[int] = Field(
        default=None, gt=0, le=86400,
        description=(
            "Target spoken duration in seconds. Mutually exclusive with"
            " target_duration. Defaults to config.narration_default_duration_seconds."
        ),
    )
    target_duration: Optional[str] = Field(
        default=None,
        description=(
            "Target duration as a preset (short/medium/long) or unit-suffixed"
            " string (5m, 120s, 0:05:00). Resolved server-side via parse_target_duration."
        ),
    )
    slug: str = Field(default="morning", min_length=1, max_length=64)


def _require_runner(app_state: AppState):
    if app_state.narration_runner is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Narration is disabled — set NARRATION_ENABLED=true and"
                " configure an LLM provider to enable the /api/narrations surface."
            ),
        )
    return app_state.narration_runner


def _resolve_target_seconds(req: CreateNarrationRequest, default_seconds: int) -> int:
    if req.target_duration_seconds is not None and req.target_duration is not None:
        bad_request("provide target_duration_seconds OR target_duration, not both")
    if req.target_duration_seconds is not None:
        return req.target_duration_seconds
    if req.target_duration is not None:
        try:
            return parse_target_duration(req.target_duration)
        except ValueError as exc:
            bad_request(str(exc))
    return default_seconds


def _narration_paths(state: AppState, narration_id: str) -> tuple[Path, Path]:
    narrations_dir = state.path_manager.narrations_dir()
    return (
        narrations_dir / f"{narration_id}.json",
        narrations_dir / f"{narration_id}.md",
    )


@router.post("", status_code=201)
async def create_narration(
    body: CreateNarrationRequest,
    app_state: AppState = Depends(get_app_state),
    user=Depends(require_auth),
):
    """Generate a narration synchronously and return its artefact paths."""
    runner = _require_runner(app_state)
    target_seconds = _resolve_target_seconds(
        body, app_state.config.narration_default_duration_seconds
    )
    try:
        run = runner.run(
            digest_id=body.digest_id,
            target_duration_seconds=target_seconds,
            slug=body.slug,
        )
    except NarrationRunnerError as exc:
        not_found("Digest", body.digest_id or "latest")
        return  # not reached — not_found raises
    stats = run.content.stats
    return api_response(
        {
            "id": run.narration_id,
            "digest_id": run.digest_id,
            "mode": run.content.mode,
            "target_duration_seconds": stats.target_duration_seconds,
            "actual_duration_seconds": round(stats.actual_duration_seconds, 2),
            "quote_count": stats.quote_count,
            "episodes_covered": run.content.episode_ids_covered,
            "episodes_in_tail": run.content.episode_ids_in_tail,
            "fallback_reason": stats.fallback_reason,
            "markdown_path": str(run.markdown_path) if run.markdown_path else None,
            "script_path": str(run.json_path) if run.json_path else None,
        }
    )


@router.get("/{narration_id}")
async def get_narration(
    narration_id: str,
    app_state: AppState = Depends(get_app_state),
    user=Depends(require_auth),
):
    """Fetch the JSON script + Markdown body for a previously-generated narration."""
    json_path, md_path = _narration_paths(app_state, narration_id)
    if not json_path.exists():
        not_found("Narration", narration_id)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("narration.read_failed", id=narration_id, error=str(exc))
        not_found("Narration", narration_id)
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else None
    return api_response(
        {
            "id": narration_id,
            "script": payload,
            "markdown": markdown,
        }
    )


@router.get("/{narration_id}/script.json")
async def get_narration_script(
    narration_id: str,
    app_state: AppState = Depends(get_app_state),
    user=Depends(require_auth),
):
    """Return the JSON script body verbatim — intended for downstream TTS consumers."""
    json_path, _ = _narration_paths(app_state, narration_id)
    if not json_path.exists():
        not_found("Narration", narration_id)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("narration.read_failed", id=narration_id, error=str(exc))
        not_found("Narration", narration_id)
    return api_response(payload)
