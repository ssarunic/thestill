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
"""Remote MCP over Streamable HTTP (spec #78).

Mounts the same MCP tool/resource surface the stdio ``thestill-mcp`` server
exposes into the FastAPI app at ``/mcp/{token}``, so claude.ai custom
connectors (the mechanism Claude mobile/web use to reach remote MCP servers)
speak the Streamable HTTP transport against that URL.

Auth model (Phase 2): the path segment is a per-user capability token —
minted from Settings, stored hashed, scoped, rate-limited, expiring. The
guard below resolves it to a user on every request and stashes the user,
the *current* admin flag and the token's scopes on the ASGI scope state;
``mcp/identity.py`` reads them inside the handlers. Possession of a URL is
still the credential, so it must only travel over HTTPS; see
specs/78-remote-mcp-access.md for the threat model. This is Claude-specific
capability auth, not standards MCP authorization (bearer tokens); OAuth is
Phase 3, keeping the same mount and session manager.
"""

from typing import TYPE_CHECKING, Optional

import structlog
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from ..mcp.identity import STATE_IS_ADMIN, STATE_SCOPES, STATE_USER
from ..mcp.resources import setup_resources
from ..mcp.tools import setup_tools
from ..services.mcp_token_service import McpTokenService
from ..utils.datetime_utils import now_utc
from .dependencies import is_effective_admin
from .middleware.rate_limit import check_mcp_token_rate_limit, resolve_client_ip

if TYPE_CHECKING:
    from ..repositories.factory import RepositoryBundle
    from ..utils.config import Config

logger = structlog.get_logger(__name__)

MOUNT_PATH = "/mcp"
RATE_LIMIT_WINDOW_SECONDS = 60


class McpHttpRuntime:
    """ASGI app guarding the Streamable HTTP MCP endpoint with per-user tokens.

    Mount at ``/mcp``; serves exactly one child path per live token,
    ``/mcp/{token}``. Anything else — unknown, revoked or expired token,
    extra path segments, non-HTTP scopes — is an empty 404 indistinguishable
    from "no such route", so the endpoint's existence leaks nothing without
    a token. Over the per-token request limit → plain 429 + Retry-After
    (the guard runs before JSON-RPC parsing, so this is an HTTP limit).

    The wrapped ``StreamableHTTPSessionManager`` runs stateless (a fresh
    transport per request, safe behind load balancers) with JSON responses.
    Its task group must be running before the first request: enter
    :meth:`lifespan` from the web app's lifespan.
    """

    def __init__(self, config: "Config", repos: "RepositoryBundle"):
        self._config = config
        self._users = repos.user
        self._tokens = McpTokenService(repos.mcp_token, ttl_days=config.mcp_token_ttl_days)

        # Same wiring as ThestillMCPServer, minus the stdio transport —
        # the stdio and HTTP servers are two doors into one room.
        server = Server("thestill-mcp")
        setup_resources(server, str(config.storage_path))
        setup_tools(server, str(config.storage_path))

        self.session_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=True,
            stateless=True,
        )
        logger.info("mcp_http_runtime_initialized", transport="streamable-http", stateless=True)

    def lifespan(self):
        """Session-manager task-group context; enter it in the app lifespan."""
        return self.session_manager.run()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Streamable HTTP is plain HTTP; refuse websockets etc.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1000})
            return

        # Starlette ≥0.33 mounts pass the full path and extend root_path
        # (per the ASGI spec) rather than rewriting scope["path"]. Strip
        # the mount prefix ourselves; what remains must be exactly one
        # path segment: the token.
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        candidate = path.strip("/")
        if not candidate or "/" in candidate:
            await Response(status_code=404)(scope, receive, send)
            return

        now = now_utc()
        # Revoked and expired collapse into "no such token" inside the SQL
        # predicate, so there is exactly one place that can get expiry wrong.
        token = self._tokens.resolve_active(candidate, now=now)
        if token is None:
            await Response(status_code=404)(scope, receive, send)
            return

        if not check_mcp_token_rate_limit(
            token.token_hash, requests_per_minute=self._config.mcp_token_requests_per_minute
        ):
            await Response(status_code=429, headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)})(
                scope, receive, send
            )
            return

        # Fresh row every request: is_admin is never cached from mint time,
        # so demotion bites on the very next call.
        user = self._users.get_by_id(token.user_id)
        if user is None:
            await Response(status_code=404)(scope, receive, send)
            return

        ip = resolve_client_ip(Request(scope, receive), config=self._config)
        self._tokens.touch_last_used(user.id, ip=ip, now=now)

        state = scope.setdefault("state", {})
        state[STATE_USER] = user
        state[STATE_IS_ADMIN] = is_effective_admin(user, self._config)
        state[STATE_SCOPES] = frozenset(token.scopes)

        await self.session_manager.handle_request(scope, receive, send)


def build_mcp_http(config: "Config", repos: "RepositoryBundle") -> Optional[McpHttpRuntime]:
    """Build the MCP HTTP runtime, or ``None`` when the feature is off."""
    if not config.mcp_http_enabled:
        return None
    return McpHttpRuntime(config, repos)


def mount_base_url(config: "Config", request_base_url: str) -> str:
    """The connector URL minus the token, e.g. ``https://host/mcp``.

    Prefers the operator-declared ``PUBLIC_BASE_URL`` (the same ground
    truth OAuth redirects use); falls back to the request's own base URL
    for direct local access. The token is appended only by the mint route,
    the one place the plaintext exists.
    """
    base = config.public_base_url or request_base_url.rstrip("/")
    return f"{base}{MOUNT_PATH}"
