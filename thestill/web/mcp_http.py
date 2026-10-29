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
capability auth, not standards MCP authorization (bearer tokens).

Spec #109 adds the standard door beside it: ``McpBearerGuard`` on the exact
path ``/mcp`` accepts OAuth access tokens (``web/mcp_oauth.py``). Both
guards end in the same ``admit``/``dispatch``, so tools see one identity
model whichever door a request came through.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Optional, Set

import structlog
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from ..mcp.identity import STATE_GPC, STATE_IS_ADMIN, STATE_SCOPES, STATE_USER
from ..models.mcp_token import SCOPE_READ
from ..models.oauth import SCOPE_OFFLINE_ACCESS
from ..services.abuse.access_guard import AccessGuard
from ..services.abuse.errors import AccountDisabledError
from ..services.mcp_token_service import McpTokenService
from ..utils.datetime_utils import now_utc
from .analytics_request import request_gpc
from .dependencies import is_effective_admin
from .middleware.rate_limit import (
    check_mcp_token_rate_limit,
    mcp_miss_budget_exhausted,
    record_mcp_token_miss,
    resolve_client_ip,
)

if TYPE_CHECKING:
    from ..models.user import User
    from ..repositories.factory import RepositoryBundle
    from ..services.mcp_oauth_service import McpOAuthService
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

    def __init__(
        self,
        config: "Config",
        repos: "RepositoryBundle",
        *,
        token_service: Optional[McpTokenService] = None,
        analytics_sink=None,
    ):
        self._config = config
        self._analytics = analytics_sink
        self._users = repos.user
        # Spec #111 — connector days for the Scorecard, written at most once
        # per reader per UTC quarter hour from this process (see
        # _record_connector_day).
        self._connector_days = repos.connector_day
        self._connector_slot: Optional[datetime] = None
        self._connector_seen: Set[str] = set()
        # Spec #104 — the same account-status rule as the web session check.
        self._access = AccessGuard(repos.user, None, multi_user=config.multi_user)
        # The app passes its own service so there is one instance per
        # process; tests and embedders may let the guard build one.
        self._tokens = token_service or McpTokenService(repos.mcp_token, ttl_days=config.mcp_token_ttl_days)

        # Imported here, not at module level: ``mcp.tools`` imports the rate
        # limiter from ``thestill.web``, whose package init imports the app
        # and, through it, this module. When the stdio entry point imports
        # ``mcp.tools`` first, a module-level import here finds it half
        # initialised and ``thestill-mcp`` dies on startup.
        from ..mcp.resources import setup_resources
        from ..mcp.tools import setup_tools

        # Same wiring as ThestillMCPServer, minus the stdio transport —
        # the stdio and HTTP servers are two doors into one room.
        server = Server("thestill-mcp")
        setup_resources(server, str(config.storage_path), config=config)
        setup_tools(server, str(config.storage_path), config=config, on_tool_called=self._count_tool_call)

        self.session_manager = StreamableHTTPSessionManager(
            app=server,
            json_response=True,
            stateless=True,
        )
        # Spec #109 — set by the app when MCP_OAUTH_ENABLED is on.
        self.bearer: Optional["McpBearerGuard"] = None
        logger.info("mcp_http_runtime_initialized", transport="streamable-http", stateless=True)

    def _count_tool_call(self, identity, tool: str, ok: bool) -> None:
        """Spec #95 ``mcp_tool_call``: remote calls by signed-in users only.
        Also records the spec #111 connector day, which needs no PostHog."""
        self._record_connector_day(identity, ok)
        if self._analytics is None or not self._analytics.enabled or identity.user is None:
            return
        try:
            from ..services.analytics_sink import McpToolCall

            self._analytics.capture(McpToolCall(tool=tool, ok=ok), user=identity.user, gpc=identity.gpc)
        except Exception as exc:  # noqa: BLE001 - e.g. a malformed tool name; never fails the call
            logger.warning("analytics_mcp_tool_call_skipped", error=str(exc))

    def _record_connector_day(self, identity, ok: bool) -> None:
        """Spec #111 §4: a successful remote call puts today on the reader's
        connector days. The day is local to the reader, so the in-process
        memo is keyed by UTC quarter hour rather than date: every IANA
        offset in use is a whole number of quarter hours (+05:30, +05:45),
        so a local midnight always starts a new slot and is never skipped,
        at the cost of at most four inserts per reader per hour. Opted-out
        readers and GPC calls store nothing (spec #103 D5); the repository
        re-checks the switch under the opt-out's lock."""
        user = identity.user
        if not ok or user is None or identity.gpc or not user.usage_analytics_enabled:
            return
        now = now_utc()
        slot = now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)
        if slot != self._connector_slot:
            self._connector_slot, self._connector_seen = slot, set()
        if user.id in self._connector_seen:
            return
        try:
            self._connector_days.record_day(user.id, now)
        except Exception:  # noqa: BLE001 - measuring a call never fails it
            logger.warning("connector_day_record_failed", user_id=user.id, exc_info=True)
            return
        self._connector_seen.add(user.id)

    @property
    def config(self) -> "Config":
        return self._config

    @property
    def users(self):
        return self._users

    def is_active(self, user: "User") -> bool:
        """Spec #104: False for a suspended or banned account."""
        try:
            self._access.ensure_active(user)
        except AccountDisabledError:
            return False
        return True

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
        if not candidate and self.bearer is not None:
            # ``/mcp/`` with a trailing slash: the same door as ``/mcp``.
            await self.bearer(scope, receive, send)
            return
        if not candidate or "/" in candidate:
            await Response(status_code=404)(scope, receive, send)
            return

        now = now_utc()
        ip = resolve_client_ip(Request(scope, receive), config=self._config)
        # An IP that has burned its miss budget gets the same 404 without a
        # database round-trip, so scanners cannot make the guard do work.
        if mcp_miss_budget_exhausted(ip):
            await Response(status_code=404)(scope, receive, send)
            return

        # Revoked and expired collapse into "no such token" inside the SQL
        # predicate, so there is exactly one place that can get expiry wrong.
        token = self._tokens.resolve_active(candidate, now=now)
        if token is None:
            record_mcp_token_miss(ip)
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
        if not await self.admit(user, scope, receive, send):
            return

        self._tokens.touch_last_used(user.id, ip=ip, now=now)
        await self.dispatch(scope, receive, send, user=user, scopes=frozenset(token.scopes))

    async def admit(self, user: "User", scope: Scope, receive: Receive, send: Send) -> bool:
        """Spec #104: a suspended or banned account is refused outright, on
        either door. Revocation on ban covers this too; this check covers a
        revocation that failed. False when a refusal was sent."""
        try:
            self._access.ensure_active(user)
        except AccountDisabledError as exc:
            await JSONResponse(status_code=exc.status_code, content={"detail": exc.detail()})(scope, receive, send)
            return False
        return True

    async def dispatch(self, scope: Scope, receive: Receive, send: Send, *, user: "User", scopes: frozenset) -> None:
        """Hand an admitted request to the MCP session manager with its
        identity on the scope state, where ``mcp/identity.py`` reads it."""
        state = scope.setdefault("state", {})
        state[STATE_USER] = user
        state[STATE_IS_ADMIN] = is_effective_admin(user, self._config)
        state[STATE_SCOPES] = scopes
        state[STATE_GPC] = request_gpc(Request(scope, receive))

        await self.session_manager.handle_request(scope, receive, send)


class McpBearerGuard:
    """ASGI app for the exact path ``/mcp``: standard bearer tokens (spec #109).

    A missing or unusable token gets ``401`` with
    ``WWW-Authenticate: Bearer resource_metadata=...``, the handshake that
    starts sign-in in Claude and other MCP clients. A valid token is the
    access token of an active grant; its own scope set (minus
    ``offline_access``, which is not an MCP scope) becomes the request's
    scopes. Account status, the admin flag and the session manager are the
    personal-URL guard's, so tools see one identity model.
    """

    def __init__(self, runtime: McpHttpRuntime, oauth: "McpOAuthService", *, resource_metadata_url: str):
        self._runtime = runtime
        self._oauth = oauth
        self._config = runtime.config
        self._users = runtime.users
        self._challenge = (
            f'Bearer resource_metadata="{resource_metadata_url}", scope="{SCOPE_READ} {SCOPE_OFFLINE_ACCESS}"'
        )

    def _unauthorized(self, error: Optional[str] = None) -> Response:
        challenge = self._challenge + (f', error="{error}"' if error else "")
        return Response(status_code=401, headers={"WWW-Authenticate": challenge})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1000})
            return

        request = Request(scope, receive)
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not token:
            # First contact: no credential yet is normal, not a miss, and the
            # challenge is cheap, so it is answered even for a burnt budget
            # (a scanner behind the same NAT must not block sign-in).
            await self._unauthorized()(scope, receive, send)
            return

        ip = resolve_client_ip(request, config=self._config)
        if mcp_miss_budget_exhausted(ip):
            await Response(status_code=429, headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)})(
                scope, receive, send
            )
            return

        found = self._oauth.verify_access(token)
        if found is None:
            record_mcp_token_miss(ip)
            await self._unauthorized("invalid_token")(scope, receive, send)
            return
        record, grant = found

        if not check_mcp_token_rate_limit(
            f"oauth-grant:{grant.id}", requests_per_minute=self._config.mcp_token_requests_per_minute
        ):
            await Response(status_code=429, headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)})(
                scope, receive, send
            )
            return

        user = self._users.get_by_id(grant.user_id)
        if user is None:
            await self._unauthorized("invalid_token")(scope, receive, send)
            return
        if not await self._runtime.admit(user, scope, receive, send):
            return

        self._oauth.touch(grant.id, ip=ip)
        scopes = frozenset(s for s in record.scopes if s != SCOPE_OFFLINE_ACCESS)
        await self._runtime.dispatch(scope, receive, send, user=user, scopes=scopes)


def build_mcp_http(
    config: "Config",
    repos: "RepositoryBundle",
    *,
    token_service: Optional[McpTokenService] = None,
    analytics_sink=None,
) -> Optional[McpHttpRuntime]:
    """Build the MCP HTTP runtime, or ``None`` when the feature is off."""
    if not config.mcp_http_enabled:
        return None
    return McpHttpRuntime(config, repos, token_service=token_service, analytics_sink=analytics_sink)


def mount_base_url(config: "Config", request_base_url: str) -> str:
    """The connector URL minus the token, e.g. ``https://host/mcp``.

    Prefers the operator-declared ``PUBLIC_BASE_URL`` (the same ground
    truth OAuth redirects use); falls back to the request's own base URL
    for direct local access. The token is appended only by the mint route,
    the one place the plaintext exists.
    """
    base = config.public_base_url or request_base_url.rstrip("/")
    return f"{base}{MOUNT_PATH}"
