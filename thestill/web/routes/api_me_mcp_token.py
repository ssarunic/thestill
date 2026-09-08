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
"""Per-user remote MCP token routes (spec #78 Phase 2).

``/api/me/mcp-token`` is user-authenticated (router-level ``require_auth``
in app.py), never admin-gated: every user mints, rotates and revokes their
own connector URL. The plaintext token appears exactly once, in the POST
response; GET never returns it.
"""

from typing import List

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...models.user import User
from ..dependencies import AppState, get_app_state, is_effective_admin, require_auth
from ..mcp_http import mount_base_url
from ..responses import api_response, bad_request

router = APIRouter()


class McpTokenRequest(BaseModel):
    """Scopes to grant. ``read`` is implicit; unknown scopes are ignored and
    ``pipeline`` is silently dropped for non-admins."""

    scopes: List[str] = Field(default_factory=list)


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _issuing_over_secure_origin(state: AppState, request: Request) -> bool:
    """The token rides in the URL path, so we only hand one out when the
    URL it will be pasted into is HTTPS (or a loopback dev address).

    ``PUBLIC_BASE_URL`` wins when set — behind a reverse proxy the request
    scheme is often plain http even though the public origin is https.
    """
    base = state.config.public_base_url
    if base:
        return base.lower().startswith("https://")
    if request.url.scheme == "https":
        return True
    return (request.url.hostname or "").lower() in _LOCAL_HOSTS


def _token_view(state: AppState, user: User) -> dict:
    service = state.mcp_token_service
    token = service.get(user.id)
    if token is None:
        return {"state": "none"}
    return {
        "state": service.state_of(token),
        "prefix": token.token_prefix,
        "scopes": list(token.scopes),
        "created_at": token.created_at.isoformat(),
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "last_used_ip": token.last_used_ip,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
    }


@router.get("")
def get_mcp_token(state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> dict:
    """The caller's connector token state — never the plaintext."""
    return api_response({"enabled": bool(state.config.mcp_http_enabled), **_token_view(state, user)})


@router.post("", status_code=201)
def create_or_rotate_mcp_token(
    body: McpTokenRequest,
    request: Request,
    state: AppState = Depends(get_app_state),
    user: User = Depends(require_auth),
) -> dict:
    """Create the caller's token, or rotate it (the old URL dies at commit).

    Returns the full connector URL once. Non-admins requesting ``pipeline``
    get it dropped, not an error.
    """
    if not state.config.mcp_http_enabled:
        bad_request("Remote MCP is disabled on this server (MCP_HTTP_ENABLED=false).")
    if not _issuing_over_secure_origin(state, request):
        bad_request(
            "Connector URLs are only issued over HTTPS (or localhost): the token travels in the URL. "
            "Set PUBLIC_BASE_URL=https://... on the server, or open this page over HTTPS."
        )
    mint = state.mcp_token_service.create_or_rotate(
        user.id,
        requested_scopes=body.scopes,
        is_admin=is_effective_admin(user, state.config),
    )
    return api_response(
        {
            "url": f"{mount_base_url(state.config, str(request.base_url))}/{mint.token}",
            "scopes": list(mint.scopes),
            "expires_at": mint.expires_at.isoformat() if mint.expires_at else None,
        }
    )


@router.delete("", status_code=204)
def revoke_mcp_token(state: AppState = Depends(get_app_state), user: User = Depends(require_auth)) -> Response:
    """Revoke without replacement. Idempotent."""
    state.mcp_token_service.revoke(user.id)
    return Response(status_code=204)


__all__ = ["router"]
