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
Per-user briefing API endpoints (spec #36).

``GET /latest`` lazy-generates: if no briefing exists or the throttle has
elapsed and new inbox items are eligible, a fresh briefing is created and
returned. The throttle in ``BriefingService`` keeps generation cost
bounded; a future spec moves this to an explicit operator trigger.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from structlog import get_logger

from ...models.briefing import Briefing
from ...models.user import User
from ...services.briefing_service import BriefingNotFoundError
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, not_found

logger = get_logger(__name__)

router = APIRouter()


def _serialize(briefing: Briefing) -> dict:
    return briefing.model_dump(mode="json")


def _require_owned(briefing: Briefing, user: User) -> None:
    """A user may only read or mutate their own briefings."""
    if briefing.user_id != user.id:
        # 404 (not 403) so an attacker can't enumerate other users' IDs
        # by probing for which briefing IDs exist.
        raise HTTPException(status_code=404, detail=f"Briefing not found: {briefing.id}")


@router.get("/latest")
async def get_latest_briefing(
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Return the user's most recent briefing, generating one if eligible.

    Returns 404 when the inbox has no eligible items in the open window —
    callers should hide the briefing card.
    """
    briefing = app_state.briefing_service.generate_for_user(user.id)
    if briefing is None:
        not_found("Briefing", "latest")
    return api_response(_serialize(briefing))


@router.get("/{briefing_id}")
async def get_briefing(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Fetch a specific briefing's metadata."""
    briefing = app_state.briefing_repository.get(briefing_id)
    if briefing is None:
        not_found("Briefing", briefing_id)
    _require_owned(briefing, user)
    return api_response(_serialize(briefing))


@router.get("/{briefing_id}/script")
async def get_briefing_script(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Return the rendered ``script.md`` body as text."""
    briefing = app_state.briefing_repository.get(briefing_id)
    if briefing is None:
        not_found("Briefing", briefing_id)
    _require_owned(briefing, user)
    if not briefing.script_path:
        not_found("Briefing script", briefing_id)
    path = Path(briefing.script_path)
    if not path.exists():
        # Row exists but the file is gone (manual cleanup, deploy mishap).
        # Surface as 404 so the UI can show a friendly empty state.
        logger.warning("briefing_script_missing", briefing_id=briefing_id, path=str(path))
        not_found("Briefing script", briefing_id)
    return api_response({"markdown": path.read_text(encoding="utf-8")})


@router.post("/{briefing_id}/listened")
async def mark_briefing_listened(
    briefing_id: str,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Set ``listened_at`` on the briefing. Idempotent."""
    existing = app_state.briefing_repository.get(briefing_id)
    if existing is None:
        not_found("Briefing", briefing_id)
    _require_owned(existing, user)
    try:
        updated = app_state.briefing_service.mark_listened(briefing_id)
    except BriefingNotFoundError:
        # Race: deleted between ownership check and update.
        not_found("Briefing", briefing_id)
    return api_response(_serialize(updated))
