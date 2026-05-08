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
Per-user inbox API endpoints.

Pagination is cursor-based on ``delivered_at`` (newest first) — clients send
the ``delivered_at`` of the last row they have as ``before`` to fetch older
rows. Read state, saved state, and dismissals live on the row.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from structlog import get_logger

from ...models.user import User
from ...services.inbox_service import (
    InboxEntryNotFoundError,
    InvalidInboxStateError,
)
from ..dependencies import AppState, get_app_state, require_auth
from ..responses import api_response, bad_request, not_found, parse_iso_datetime

logger = get_logger(__name__)

router = APIRouter()


_MAX_LIMIT = 200


class InboxStateRequest(BaseModel):
    """Body for ``POST /api/inbox/{episode_id}/state``."""

    state: str


@router.get("")
async def list_inbox(
    state: Optional[str] = None,
    limit: int = 50,
    before: Optional[str] = None,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """List the current user's inbox, newest delivery first."""
    if limit <= 0 or limit > _MAX_LIMIT:
        bad_request(f"limit must be between 1 and {_MAX_LIMIT}")

    before_dt = parse_iso_datetime(before, field_name="before")

    try:
        items = app_state.inbox_service.list(user.id, state=state, limit=limit, before=before_dt)
    except InvalidInboxStateError as exc:
        bad_request(str(exc))

    next_before = items[-1].entry.delivered_at.isoformat() if len(items) == limit else None

    return api_response(
        {
            "items": [item.model_dump(mode="json") for item in items],
            "count": len(items),
            "next_before": next_before,
        }
    )


@router.get("/unread-count")
async def get_unread_count(
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Lightweight unread count for badge rendering."""
    return api_response({"unread_count": app_state.inbox_service.unread_count(user.id)})


@router.post("/{episode_id}/state")
async def set_inbox_state(
    episode_id: str,
    body: InboxStateRequest,
    app_state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
):
    """Set ``read`` / ``unread`` / ``saved`` / ``dismissed`` on an inbox row."""
    try:
        entry = app_state.inbox_service.mark_state(user.id, episode_id, body.state)
    except InvalidInboxStateError as exc:
        bad_request(str(exc))
    except InboxEntryNotFoundError:
        not_found("Inbox entry", episode_id)

    return api_response({"entry": entry.model_dump(mode="json")})
