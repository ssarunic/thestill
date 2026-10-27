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

"""Consent and connected-apps routes for the MCP OAuth server (spec #109).

Both routers are user-authenticated (router-level ``require_auth`` in
app.py). The session cookie is ``SameSite=strict``, so it is not sent on
the cross-site navigation from an MCP client to ``/authorize``; that route
only parks the request, and the consent page reads and decides it through
these same-origin calls. An OAuth access token is never a credential here:
``require_auth`` accepts only the web session.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ...models.oauth import SCOPE_OFFLINE_ACCESS
from ...models.user import User
from ...services.mcp_oauth_service import McpOAuthService
from ..dependencies import AppState, get_app_state, is_effective_admin, require_auth
from ..responses import api_response, not_found

pending_router = APIRouter()
grants_router = APIRouter()


class ApproveRequest(BaseModel):
    """The MCP scopes the reader ticked. ``read`` is implicit; anything the
    client did not ask for, or ``pipeline`` for a non-admin, is dropped."""

    scopes: List[str] = Field(default_factory=list)


def _service(state: AppState) -> McpOAuthService:
    if state.mcp_oauth_service is None:
        not_found("OAuth", "disabled")
    return state.mcp_oauth_service


@pending_router.get("/{request_id}")
def get_pending(request_id: str, state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> dict:
    """What the consent screen shows. 404 once the request expired or was decided."""
    view = _service(state).view_pending(request_id)
    if view is None:
        not_found("Authorization request", "expired")
    return api_response(
        {
            "client_name": view.client_name,
            "redirect_host": view.redirect_host,
            "loopback_only": view.loopback_only,
            "requested_scopes": list(view.requested_scopes),
            "offline_access": view.offline_access,
        }
    )


@pending_router.post("/{request_id}/approve")
def approve_pending(
    request_id: str,
    body: ApproveRequest,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """Approve for the signed-in reader; the client redirect to follow."""
    redirect = _service(state).approve(
        request_id, user_id=user.id, is_admin=is_effective_admin(user, state.config), ticked=body.scopes
    )
    if redirect is None:
        not_found("Authorization request", "expired")
    return api_response({"redirect": redirect})


@pending_router.post("/{request_id}/deny")
def deny_pending(request_id: str, state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> dict:
    """Refuse; the client redirect (carrying ``access_denied``) to follow."""
    redirect = _service(state).deny(request_id)
    if redirect is None:
        not_found("Authorization request", "expired")
    return api_response({"redirect": redirect})


@grants_router.get("")
def list_grants(state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> dict:
    """The reader's connected apps. Empty when OAuth is off."""
    if state.mcp_oauth_service is None:
        return api_response({"grants": []})
    return api_response(
        {
            "grants": [
                {
                    "id": g.id,
                    "client_name": g.client_name,
                    "redirect_host": g.redirect_host,
                    "scopes": [s for s in g.scopes if s != SCOPE_OFFLINE_ACCESS],
                    "created_at": g.created_at.isoformat(),
                    "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None,
                }
                for g in state.mcp_oauth_service.list_grants(user.id)
            ]
        }
    )


@grants_router.delete("/{grant_id}", status_code=204)
def disconnect_grant(
    grant_id: str, state: AppState = Depends(get_app_state), user: User = Depends(require_auth)
) -> Response:
    """Disconnect one app; its tokens stop working on the next call."""
    try:
        uuid.UUID(grant_id)  # Postgres would raise on a malformed uuid
    except ValueError:
        not_found("Connected app", grant_id)
    if not _service(state).disconnect(user.id, grant_id):
        not_found("Connected app", grant_id)
    return Response(status_code=204)


__all__ = ["pending_router", "grants_router"]
