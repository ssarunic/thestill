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

"""MCP SDK adapter for thestill's OAuth authorization server (spec #109).

The MCP SDK (2.2.0) provides the OAuth endpoints (``/authorize``,
``/token``, ``/register``, ``/revoke``) behind an async provider protocol;
``ThestillOAuthProvider`` implements it by calling the synchronous
``McpOAuthService`` from a worker thread. Three things the SDK does not do
the way Claude needs are patched here, not forked:

- **The client record.** The SDK checks scope and redirect on the client
  record *before* calling ``authorize()``. ``ThestillClient`` sets the
  record's scope from our policy (a metadata document without ``scope``
  would otherwise be allowed none) and lets loopback redirects match on
  any port (Claude Code's callback port changes every session).
- **Authorization server metadata.** The SDK hard-codes
  ``token_endpoint_auth_methods_supported`` without ``none``; Claude uses a
  Client ID Metadata Document only when ``none`` is listed and
  ``client_id_metadata_document_supported`` is true. We serve our own.
- **Registration defaults.** A registration that names no
  ``token_endpoint_auth_method`` becomes a public client (``none``), not
  the SDK's ``client_secret_post``; an explicit request for a secret is
  honoured (decision 4 in the spec).
"""

import json
from functools import partial
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

import anyio.to_thread
import structlog
from mcp.server.auth.handlers.metadata import MetadataHandler
from mcp.server.auth.handlers.register import RegistrationHandler
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.server.auth.routes import (
    REGISTRATION_PATH,
    REVOCATION_PATH,
    cors_middleware,
    create_auth_routes,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from ..models.oauth import OAuthClientRecord
from ..services.mcp_oauth_service import (
    SUPPORTED_SCOPES,
    IssuedTokens,
    McpOAuthService,
    OAuthRequestError,
    client_scope_ceiling,
)
from .middleware.rate_limit import allow_oauth_registration, resolve_client_ip

logger = structlog.get_logger(__name__)

CONSENT_PATH = "/oauth/consent"
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_PATH = "/.well-known/oauth-authorization-server"
# Paths the OAuth routes own, for the SPA catch-all's skip list.
OAUTH_PATHS = ("authorize", "token", "register", "revoke")
_REGISTRATION_BODY_MAX = 16 * 1024
_REVOCATION_BODY_MAX = 64 * 1024
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_AUTH_METHODS = ["none", "client_secret_post", "client_secret_basic"]


def _loopback_port_match(requested: str, registered: List[AnyUrl]) -> bool:
    """RFC 8252 §7.3: an ``http`` loopback redirect matches a registered one
    that differs only in port. Never applies to ``https`` or other hosts."""
    want = urlsplit(requested)
    if want.scheme != "http" or (want.hostname or "") not in _LOOPBACK_HOSTS:
        return False
    for uri in registered:
        have = urlsplit(str(uri))
        if (
            have.scheme == "http"
            and have.hostname == want.hostname
            and (have.path or "/") == (want.path or "/")
            and have.query == want.query
        ):
            return True
    return False


class ThestillClient(OAuthClientInformationFull):
    """An SDK client record carrying thestill's scope and redirect policy."""

    @classmethod
    def from_record(cls, record: OAuthClientRecord) -> Optional["ThestillClient"]:
        data = {**record.metadata, "client_id": record.client_id}
        scope = client_scope_ceiling(data.get("scope"))
        if scope is None:
            return None
        data["scope"] = scope
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            logger.warning("oauth_client_record_invalid", client_id=record.client_id, error=str(exc))
            return None

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if (
            redirect_uri is not None
            and self.redirect_uris
            and redirect_uri not in self.redirect_uris
            and _loopback_port_match(str(redirect_uri), self.redirect_uris)
        ):
            return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


class _PublicClientRevocation:
    """ASGI wrapper for the SDK's ``/revoke``.

    SDK 2.2.0's ``RevocationRequest`` makes ``client_secret`` a required
    field, so a public client that rightly omits it (RFC 7009) gets a 400.
    This supplies the empty value; the client authenticator, which runs
    first, has already decided whether a secret was needed.
    """

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if len(body) > _REVOCATION_BODY_MAX:
                await Response(status_code=413)(scope, receive, send)
                return
            if not message.get("more_body"):
                break
        if b"client_secret=" not in body:
            body += b"&client_secret=" if body else b"client_secret="

        async def replay():
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)


def _oauth_token(issued: IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        scope=" ".join(issued.scopes),
        refresh_token=issued.refresh_token,
    )


class ThestillOAuthProvider:
    """``OAuthAuthorizationServerProvider`` over ``McpOAuthService``.

    The service and its repositories are synchronous, so every call runs on
    a worker thread; nothing here touches the database directly.
    """

    def __init__(self, service: McpOAuthService, *, base_url: str):
        self._service = service
        self._base_url = base_url.rstrip("/")

    async def _run(self, fn, *args, **kwargs):
        return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        record = await self._run(self._service.get_client, client_id)
        return ThestillClient.from_record(record) if record is not None else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        metadata = client_info.model_dump(mode="json", exclude_none=True)
        await self._run(self._service.register_client, client_info.client_id, metadata)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        try:
            request_id = await self._run(
                self._service.park,
                client_id=client.client_id,
                redirect_uri=str(params.redirect_uri),
                redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                state=params.state,
                scopes=params.scopes,
                code_challenge=params.code_challenge,
                resource=params.resource,
            )
        except OAuthRequestError as exc:
            raise AuthorizeError(error="invalid_request", error_description=exc.description) from exc
        # The id rides in the fragment so it never reaches a log or a Referer.
        return f"{self._base_url}{CONSENT_PATH}#request={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        record = await self._run(self._service.load_code, client.client_id, authorization_code)
        if record is None:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=list(record.scopes),
            expires_at=record.expires_at.timestamp(),
            client_id=record.client_id,
            code_challenge=record.code_challenge,
            redirect_uri=AnyUrl(record.redirect_uri),
            redirect_uri_provided_explicitly=record.redirect_uri_explicit,
            resource=record.resource,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        try:
            return _oauth_token(await self._run(self._service.exchange_code, authorization_code.code))
        except OAuthRequestError as exc:
            raise TokenError(error="invalid_grant", error_description=exc.description) from exc

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        record = await self._run(self._service.load_refresh, client.client_id, refresh_token)
        if record is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=list(record.scopes),
            expires_at=int(record.expires_at.timestamp()) if record.expires_at else None,
            resource=record.resource,
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: List[str]
    ) -> OAuthToken:
        try:
            return _oauth_token(await self._run(self._service.exchange_refresh, refresh_token.token, scopes))
        except OAuthRequestError as exc:
            raise TokenError(error="invalid_grant", error_description=exc.description) from exc

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        found = await self._run(self._service.verify_access, token)
        if found is None:
            return None
        record, grant = found
        return AccessToken(
            token=token,
            client_id=record.client_id,
            scopes=list(record.scopes),
            expires_at=int(record.expires_at.timestamp()) if record.expires_at else None,
            resource=record.resource,
            subject=grant.user_id,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await self._run(self._service.revoke_token, token.token)


def _authorization_server_metadata(base: str) -> OAuthMetadata:
    # Strings, not AnyHttpUrl: the model keeps an empty path empty only when
    # it parses the URL itself, and RFC 8414 compares the issuer exactly.
    return OAuthMetadata.model_validate(
        dict(
            issuer=base,
            authorization_endpoint=f"{base}/authorize",
            token_endpoint=f"{base}/token",
            registration_endpoint=f"{base}{REGISTRATION_PATH}",
            revocation_endpoint=f"{base}/revoke",
            scopes_supported=list(SUPPORTED_SCOPES),
            response_types_supported=["code"],
            grant_types_supported=["authorization_code", "refresh_token"],
            token_endpoint_auth_methods_supported=_AUTH_METHODS,
            revocation_endpoint_auth_methods_supported=_AUTH_METHODS,
            code_challenge_methods_supported=["S256"],
            client_id_metadata_document_supported=True,
        )
    )


def build_oauth_routes(service: McpOAuthService, *, base_url: str, config) -> List[BaseRoute]:
    """The OAuth routes to register ahead of the ``/mcp`` mount and the SPA.

    The SDK's routes, minus its authorization server metadata (replaced) and
    its registration endpoint (wrapped to default to public clients and
    rate-limited per IP); plus protected resource metadata at both the
    path-suffixed and the root well-known location.
    """
    base = base_url.rstrip("/")
    provider = ThestillOAuthProvider(service, base_url=base)
    registration = ClientRegistrationOptions(
        enabled=True, valid_scopes=list(SUPPORTED_SCOPES), default_scopes=list(SUPPORTED_SCOPES)
    )
    sdk_routes = create_auth_routes(
        provider,
        issuer_url=AnyHttpUrl(base),
        client_registration_options=registration,
        revocation_options=RevocationOptions(enabled=True),
    )
    replaced = (AUTHORIZATION_SERVER_PATH, REGISTRATION_PATH, REVOCATION_PATH)
    keep = [r for r in sdk_routes if getattr(r, "path", None) not in replaced]
    sdk_revoke = next(r for r in sdk_routes if getattr(r, "path", None) == REVOCATION_PATH)

    metadata = MetadataHandler(_authorization_server_metadata(base))
    register = RegistrationHandler(provider, options=registration)

    async def register_endpoint(request: Request) -> Response:
        if not allow_oauth_registration(resolve_client_ip(request, config=config)):
            return JSONResponse(
                {"error": "invalid_client_metadata", "error_description": "too many registrations"},
                status_code=429,
                headers={"Retry-After": "3600"},
            )
        body = await request.body()
        if len(body) > _REGISTRATION_BODY_MAX:
            return JSONResponse({"error": "invalid_client_metadata"}, status_code=413)
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict) and not data.get("token_endpoint_auth_method"):
            data["token_endpoint_auth_method"] = "none"
            body = json.dumps(data).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return await register.handle(Request(request.scope, receive))

    resource_routes = create_protected_resource_routes(
        resource_url=f"{base}/mcp",
        authorization_servers=[base],
        scopes_supported=list(SUPPORTED_SCOPES),
        resource_name="thestill",
    )
    # The same document at the root, for clients that probe there first.
    root_resource = Route(PROTECTED_RESOURCE_PATH, endpoint=resource_routes[0].endpoint, methods=["GET", "OPTIONS"])

    return [
        *keep,
        Route(
            AUTHORIZATION_SERVER_PATH,
            endpoint=cors_middleware(metadata.handle, ["GET", "OPTIONS"]),
            methods=["GET", "OPTIONS"],
        ),
        Route(
            REGISTRATION_PATH,
            endpoint=cors_middleware(register_endpoint, ["POST", "OPTIONS"]),
            methods=["POST", "OPTIONS"],
        ),
        Route(REVOCATION_PATH, endpoint=_PublicClientRevocation(sdk_revoke.app), methods=["POST", "OPTIONS"]),
        *resource_routes,
        root_resource,
    ]


def resource_metadata_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{PROTECTED_RESOURCE_PATH}/mcp"


def build_mcp_oauth(config, repos, runtime) -> Tuple[McpOAuthService, List[BaseRoute]]:
    """The OAuth service and every route spec #109 adds, ahead of the
    ``/mcp`` mount: the OAuth endpoints, both metadata documents and the
    bearer guard on the exact path ``/mcp`` (also installed on ``runtime``
    for ``/mcp/``)."""
    from ..services.abuse.access_guard import AccessGuard
    from ..services.abuse.errors import AccountDisabledError
    from .mcp_http import McpBearerGuard

    base = config.public_base_url.rstrip("/")
    guard = AccessGuard(repos.user, None, multi_user=config.multi_user)

    def is_user_active(user_id: str) -> bool:
        user = repos.user.get_by_id(user_id)
        if user is None:
            return False
        try:
            guard.ensure_active(user)
        except AccountDisabledError:
            return False
        return True

    service = McpOAuthService(
        repos.oauth,
        resource_url=f"{base}/mcp",
        refresh_ttl_days=config.mcp_token_ttl_days,
        is_user_active=is_user_active,
    )
    bearer = McpBearerGuard(runtime, service, resource_metadata_url=resource_metadata_url(base))
    runtime.bearer = bearer
    routes = [*build_oauth_routes(service, base_url=base, config=config), Route("/mcp", endpoint=bearer)]
    return service, routes
