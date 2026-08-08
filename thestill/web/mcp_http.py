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

"""Remote MCP over Streamable HTTP (spec #71 Phase 1).

Mounts the same MCP tool/resource surface the stdio server
(``thestill-mcp``) exposes onto the FastAPI web server, behind a
capability URL: ``/mcp/{MCP_HTTP_SECRET}``. claude.ai custom connectors
(the mechanism Claude mobile/web use to reach remote MCP servers) speak
the Streamable HTTP transport against that URL.

Phase 1 auth model: possession of the URL is the credential, and it is
operator-equivalent — see specs/71-remote-mcp-access.md for the threat
model. Phase 2 replaces the path-secret guard with OAuth 2.1 while
keeping the same mount and session manager.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import structlog
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from ..mcp.resources import setup_resources
from ..mcp.tools import setup_tools

if TYPE_CHECKING:
    from ..utils.config import Config

logger = structlog.get_logger(__name__)

# Matches the load_config() gate — duplicated here so a Config constructed
# directly (tests, embedding callers) can't mount a guessable endpoint.
MIN_SECRET_LENGTH = 32

MOUNT_PATH = "/mcp"


class McpHttpRuntime:
    """ASGI app guarding the Streamable HTTP MCP endpoint with a path secret.

    Mount at ``/mcp``; serves exactly one child path, ``/mcp/{secret}``.
    Anything else — wrong secret, extra path segments, non-HTTP scopes —
    is a 404 indistinguishable from "no such route", so the endpoint's
    existence leaks nothing without the secret.

    The wrapped ``StreamableHTTPSessionManager`` runs stateless (a fresh
    transport per request, safe behind load balancers — claude.ai's client
    handles stateless servers) with JSON responses (no SSE stream needed
    in stateless mode). Its task group must be running before the first
    request: enter :meth:`lifespan` from the web app's lifespan.
    """

    def __init__(self, config: "Config"):
        if len(config.mcp_http_secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"mcp_http_secret must be at least {MIN_SECRET_LENGTH} characters "
                "(generate one with: openssl rand -hex 32)"
            )
        self._secret = config.mcp_http_secret

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
        # path segment equal to the secret. compare_digest keeps the
        # check constant-time for equal-length candidates.
        path = scope["path"]
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        candidate = path.strip("/")
        if not candidate or "/" in candidate or not secrets.compare_digest(candidate, self._secret):
            await Response(status_code=404)(scope, receive, send)
            return

        await self.session_manager.handle_request(scope, receive, send)


def build_mcp_http(config: "Config") -> McpHttpRuntime | None:
    """Build the MCP HTTP runtime, or ``None`` when the feature is off."""
    if not config.mcp_http_enabled:
        return None
    return McpHttpRuntime(config)


def connector_url(config: "Config", request_base_url: str) -> str:
    """The capability URL to paste into claude.ai's custom-connector form.

    Prefers the operator-declared ``PUBLIC_BASE_URL`` (the same ground
    truth OAuth redirects use); falls back to the request's own base URL
    for direct local access.
    """
    base = config.public_base_url or request_base_url.rstrip("/")
    return f"{base}{MOUNT_PATH}/{config.mcp_http_secret}"
