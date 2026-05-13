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

"""Narrated-briefing artefact endpoints (spec #33).

These are direct-fetch endpoints for the on-disk artefacts, intended
for the future TTS consumer and for clients that want to deep-link to
a specific narration variant. The user-facing trigger now lives at
``POST /api/briefings/{briefing_id}/narrate`` so the briefing record stays
the durable join key for narrations.
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from structlog import get_logger

from ...models.briefing import Briefing
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, not_found

logger = get_logger(__name__)

router = APIRouter()


def _narration_paths(state: AppState, narration_id: str) -> tuple[Path, Path]:
    narrations_dir = state.path_manager.narrations_dir()
    return (
        narrations_dir / f"{narration_id}.json",
        narrations_dir / f"{narration_id}.md",
    )


def _resolve_owned_briefing(state: AppState, narration_id: str, user_id: str) -> Optional[Briefing]:
    """Resolve the briefing that owns ``narration_id`` and check ownership.

    Filenames are ``<briefing_id>-<slug>.{json,md}``. Both the briefing_id
    (UUID4) and the slug may contain hyphens, so we iterate possible
    split points from longest briefing_id prefix down to shortest, hitting
    the repo until a row matches. Returns ``None`` when no row matches
    or the matching briefing belongs to another user — callers translate
    both into a 404 so the endpoint isn't an enumeration oracle.
    """
    parts = narration_id.split("-")
    if len(parts) < 2:
        return None
    for n in range(len(parts) - 1, 0, -1):
        candidate = "-".join(parts[:n])
        briefing = state.briefing_repository.get_by_id(candidate)
        if briefing is None:
            continue
        if briefing.user_id != user_id:
            return None
        return briefing
    return None


@router.get("/{narration_id}")
async def get_narration(
    narration_id: str,
    app_state: AppState = Depends(get_app_state),
    user=Depends(require_auth),
):
    """Fetch the JSON script + Markdown body for a stored narration."""
    if _resolve_owned_briefing(app_state, narration_id, user.id) is None:
        not_found("Narration", narration_id)
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
    if _resolve_owned_briefing(app_state, narration_id, user.id) is None:
        not_found("Narration", narration_id)
    json_path, _ = _narration_paths(app_state, narration_id)
    if not json_path.exists():
        not_found("Narration", narration_id)
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("narration.read_failed", id=narration_id, error=str(exc))
        not_found("Narration", narration_id)
    return api_response(payload)
