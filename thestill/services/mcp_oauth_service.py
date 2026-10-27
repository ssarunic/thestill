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
The OAuth authorization server behind the remote MCP server (spec #109).

Synchronous and SDK-free: the async MCP SDK provider in
``web/mcp_oauth.py`` calls into this from a worker thread, and the consent
and Settings routes call it directly. Everything that decides who may do
what lives here:

- **Scopes.** The MCP scopes go through ``McpTokenService.normalize_scopes``
  (``read`` always, ``pipeline`` admins only), narrowed to what the client
  asked for. ``offline_access`` is not an MCP scope and that helper would
  drop it, so it is carried separately: kept only when requested, and it
  is what earns a refresh token.
- **Grants.** Approving keeps the reader's grant for the client when the
  approved set is unchanged and replaces it otherwise, which revokes every
  code and token issued under the old one (repository ``approve``).
- **Credentials.** Opaque random strings, stored as SHA-256 hashes; each
  carries its own scopes. Access tokens last an hour; refresh tokens rotate
  on every use, and presenting a retired one revokes the grant.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode, urlsplit, urlunsplit

from structlog import get_logger

from ..models.mcp_token import ALL_SCOPES, SCOPE_READ
from ..models.oauth import (
    SCOPE_OFFLINE_ACCESS,
    OAuthClientRecord,
    OAuthCode,
    OAuthGrant,
    OAuthPending,
    OAuthTokenRecord,
    RevokeReason,
)
from ..repositories.oauth_repository import OAuthRepository
from ..utils.datetime_utils import now_utc
from .mcp_token_service import McpTokenService
from .oauth_cimd import CimdResolver, is_cimd_client_id

logger = get_logger(__name__)

PENDING_TTL = timedelta(minutes=10)
CODE_TTL = timedelta(seconds=60)
ACCESS_TTL = timedelta(hours=1)
# Retired refresh tokens and revoked grants are kept this long so a replay
# is still recognised as one; DCR registrations nobody used go after a week.
RETIRED_KEEP = timedelta(days=30)
UNUSED_CLIENT_KEEP = timedelta(days=7)
TOUCH_INTERVAL_SECONDS = 60

#: Every scope a client may be registered with or request.
SUPPORTED_SCOPES: Tuple[str, ...] = (*ALL_SCOPES, SCOPE_OFFLINE_ACCESS)
#: What a request that names no scope gets.
DEFAULT_REQUEST_SCOPES: Tuple[str, ...] = (SCOPE_READ, SCOPE_OFFLINE_ACCESS)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})


class OAuthRequestError(Exception):
    """A request the authorization server refuses (maps to an OAuth error)."""

    def __init__(self, error: str, description: str):
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    scopes: Tuple[str, ...]
    refresh_token: Optional[str] = None


@dataclass(frozen=True)
class PendingView:
    """What the consent screen shows for a parked request."""

    client_name: Optional[str]
    redirect_host: str
    loopback_only: bool
    requested_scopes: Tuple[str, ...]
    offline_access: bool


def hash_secret(plaintext: str) -> str:
    return McpTokenService.hash_token(plaintext)


def client_scope_ceiling(declared: Optional[str]) -> Optional[str]:
    """The scope string a client record may request from.

    A client that declares nothing gets every supported scope (registration
    is a ceiling; the consent screen decides). A declared set is narrowed to
    what we support; None when nothing is left, which makes the client
    unusable.
    """
    if declared is None:
        return " ".join(SUPPORTED_SCOPES)
    wanted = set(declared.split())
    kept = [s for s in SUPPORTED_SCOPES if s in wanted]
    return " ".join(kept) if kept else None


def approved_scopes(requested: Sequence[str], ticked: Iterable[str], *, is_admin: bool) -> Tuple[str, ...]:
    """The set a reader approves: ticked MCP scopes the client asked for,
    normalised, plus ``offline_access`` when the client asked for it."""
    asked = set(requested)
    mcp = McpTokenService.normalize_scopes([s for s in ticked if s in asked], is_admin=is_admin)
    return (*mcp, SCOPE_OFFLINE_ACCESS) if SCOPE_OFFLINE_ACCESS in asked else tuple(mcp)


def redirect_host(uri: str) -> str:
    return urlsplit(uri).hostname or uri


def is_loopback(uri: str) -> bool:
    parts = urlsplit(uri)
    return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK_HOSTS


def with_query(uri: str, **params: Optional[str]) -> str:
    """``uri`` with ``params`` appended to its query (None values skipped)."""
    parts = urlsplit(uri)
    extra = urlencode({k: v for k, v in params.items() if v is not None})
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


class McpOAuthService:
    """Authorization server logic over an ``OAuthRepository``."""

    def __init__(
        self,
        repository: OAuthRepository,
        *,
        resource_url: str,
        refresh_ttl_days: int,
        is_user_active: Callable[[str], bool],
        cimd: Optional[CimdResolver] = None,
        clock: Callable = now_utc,
    ):
        self._repo = repository
        self.resource_url = resource_url
        # 0 means refresh tokens never expire (same switch as MCP_TOKEN_TTL_DAYS).
        self._refresh_ttl = timedelta(days=refresh_ttl_days) if refresh_ttl_days > 0 else None
        self._is_user_active = is_user_active
        self._cimd = cimd or CimdResolver(repository, clock=clock)
        self._clock = clock

    # -- clients -------------------------------------------------------------

    def get_client(self, client_id: str) -> Optional[OAuthClientRecord]:
        """A registered client, or a metadata-document client resolved
        through the cache. None when unknown or unusable."""
        if is_cimd_client_id(client_id):
            return self._cimd.resolve(client_id)
        record = self._repo.get_client(client_id)
        return record if record is not None and record.source == "dcr" else None

    def register_client(self, client_id: str, metadata: dict) -> None:
        """Store a dynamic registration (metadata as the SDK built it)."""
        if is_cimd_client_id(client_id):
            raise OAuthRequestError("invalid_client_metadata", "client_id must not be a URL")
        name = metadata.get("client_name")
        self._repo.save_client(
            OAuthClientRecord(
                client_id=client_id,
                source="dcr",
                client_name=name if isinstance(name, str) else None,
                metadata=metadata,
                created_at=self._clock(),
            )
        )

    # -- authorization ---------------------------------------------------------

    def park(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        redirect_uri_explicit: bool,
        state: Optional[str],
        scopes: Optional[Sequence[str]],
        code_challenge: str,
        resource: Optional[str],
    ) -> str:
        """Store an authorization request; return the plaintext id for the
        consent page's fragment."""
        if resource is not None and resource.rstrip("/") != self.resource_url.rstrip("/"):
            raise OAuthRequestError("invalid_request", "resource is not this server's MCP endpoint")
        request_id = secrets.token_urlsafe(20)  # 160 bits
        now = self._clock()
        self._repo.save_pending(
            OAuthPending(
                id_hash=hash_secret(request_id),
                client_id=client_id,
                redirect_uri=redirect_uri,
                redirect_uri_explicit=redirect_uri_explicit,
                state=state,
                scopes=tuple(scopes) if scopes else DEFAULT_REQUEST_SCOPES,
                code_challenge=code_challenge,
                resource=resource,
                created_at=now,
                expires_at=now + PENDING_TTL,
            )
        )
        return request_id

    def view_pending(self, request_id: str) -> Optional[PendingView]:
        pending = self._repo.get_pending(hash_secret(request_id), now=self._clock())
        if pending is None:
            return None
        client = self.get_client(pending.client_id)
        redirect_uris = (client.metadata.get("redirect_uris") or []) if client else []
        return PendingView(
            client_name=client.client_name if client else None,
            redirect_host=redirect_host(pending.redirect_uri),
            loopback_only=bool(redirect_uris) and all(is_loopback(u) for u in redirect_uris),
            requested_scopes=tuple(s for s in pending.scopes if s in ALL_SCOPES),
            offline_access=SCOPE_OFFLINE_ACCESS in pending.scopes,
        )

    def approve(self, request_id: str, *, user_id: str, is_admin: bool, ticked: Iterable[str]) -> Optional[str]:
        """Approve for ``user_id``; the client redirect carrying the code,
        or None when the request is gone (expired or already decided)."""
        id_hash = hash_secret(request_id)
        now = self._clock()
        pending = self._repo.get_pending(id_hash, now=now)
        if pending is None:
            return None
        scopes = approved_scopes(pending.scopes, ticked, is_admin=is_admin)
        client = self.get_client(pending.client_id)
        code = secrets.token_urlsafe(32)
        result = self._repo.approve(
            id_hash,
            now=now,
            user_id=user_id,
            new_grant_id=str(uuid.uuid4()),
            client_name=client.client_name if client else None,
            redirect_host=redirect_host(pending.redirect_uri),
            scopes=scopes,
            code_hash=hash_secret(code),
            code_expires_at=now + CODE_TTL,
        )
        if result is None:
            return None
        grant, _ = result
        logger.info("oauth_grant_approved", grant_id=grant.id, client_id=pending.client_id, scopes=list(scopes))
        return with_query(pending.redirect_uri, code=code, state=pending.state)

    def deny(self, request_id: str) -> Optional[str]:
        """The client redirect carrying ``access_denied``, or None when the
        request is gone."""
        id_hash = hash_secret(request_id)
        pending = self._repo.get_pending(id_hash, now=self._clock())
        if pending is None or not self._repo.delete_pending(id_hash):
            return None
        return with_query(pending.redirect_uri, error="access_denied", state=pending.state)

    # -- codes -------------------------------------------------------------------

    def load_code(self, client_id: str, code: str) -> Optional[OAuthCode]:
        """The redeemable code, or None. A used code coming back means it
        leaked: the grant behind it is revoked."""
        found = self._repo.get_code(hash_secret(code))
        if found is None:
            return None
        record, grant = found
        if record.client_id != client_id:
            return None
        now = self._clock()
        if record.used_at is not None:
            if self._repo.revoke_grant(grant.id, now=now, reason="reuse"):
                logger.warning("oauth_code_replayed", grant_id=grant.id, client_id=client_id)
            return None
        if record.expires_at <= now or grant.revoked_at is not None or not self._is_user_active(grant.user_id):
            return None
        return record

    def exchange_code(self, code: str) -> IssuedTokens:
        found = self._repo.get_code(hash_secret(code))
        if found is None:
            raise OAuthRequestError("invalid_grant", "authorization code is not valid")
        record, grant = found
        now = self._clock()
        tokens, issued = self._mint(grant, record.client_id, record.scopes, record.resource, now)
        if not self._repo.redeem_code(record.code_hash, now=now, tokens=tokens):
            raise OAuthRequestError("invalid_grant", "authorization code is not valid")
        return issued

    # -- refresh -----------------------------------------------------------------

    def load_refresh(self, client_id: str, token: str) -> Optional[OAuthTokenRecord]:
        """The usable refresh token, or None. A retired one coming back means
        it leaked: the grant behind it is revoked."""
        found = self._repo.get_token(hash_secret(token))
        if found is None:
            return None
        record, grant = found
        if record.kind != "refresh" or record.client_id != client_id:
            return None
        now = self._clock()
        if record.replaced_by_hash is not None:
            if self._repo.revoke_grant(grant.id, now=now, reason="reuse"):
                logger.warning("oauth_refresh_replayed", grant_id=grant.id, client_id=client_id)
            return None
        if record.revoked_at is not None or grant.revoked_at is not None:
            return None
        if record.expires_at is not None and record.expires_at <= now:
            return None
        if not self._is_user_active(grant.user_id):
            return None
        return record

    def exchange_refresh(self, token: str, scopes: Sequence[str]) -> IssuedTokens:
        """Rotate: new access token with ``scopes`` (the SDK has checked they
        are within the refresh token's), new refresh token with the old
        one's full set."""
        found = self._repo.get_token(hash_secret(token))
        if found is None:
            raise OAuthRequestError("invalid_grant", "refresh token is not valid")
        record, grant = found
        now = self._clock()
        tokens, issued = self._mint(
            grant, record.client_id, tuple(scopes) or record.scopes, record.resource, now, refresh_scopes=record.scopes
        )
        if not self._repo.rotate_refresh(record.token_hash, now=now, tokens=tokens):
            raise OAuthRequestError("invalid_grant", "refresh token is not valid")
        return issued

    def _mint(
        self,
        grant: OAuthGrant,
        client_id: str,
        scopes: Tuple[str, ...],
        resource: Optional[str],
        now,
        *,
        refresh_scopes: Optional[Tuple[str, ...]] = None,
    ) -> Tuple[List[OAuthTokenRecord], IssuedTokens]:
        access = secrets.token_urlsafe(32)
        tokens = [
            OAuthTokenRecord(
                token_hash=hash_secret(access),
                grant_id=grant.id,
                client_id=client_id,
                kind="access",
                scopes=scopes,
                resource=resource,
                created_at=now,
                expires_at=now + ACCESS_TTL,
            )
        ]
        refresh = None
        offline_set = refresh_scopes if refresh_scopes is not None else scopes
        if SCOPE_OFFLINE_ACCESS in offline_set:
            refresh = secrets.token_urlsafe(32)
            tokens.append(
                OAuthTokenRecord(
                    token_hash=hash_secret(refresh),
                    grant_id=grant.id,
                    client_id=client_id,
                    kind="refresh",
                    scopes=offline_set,
                    resource=resource,
                    created_at=now,
                    expires_at=now + self._refresh_ttl if self._refresh_ttl else None,
                )
            )
        issued = IssuedTokens(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            scopes=scopes,
            refresh_token=refresh,
        )
        return tokens, issued

    # -- access ------------------------------------------------------------------

    def verify_access(self, token: str) -> Optional[Tuple[OAuthTokenRecord, OAuthGrant]]:
        """The bearer guard's lookup: an unexpired access token under an
        active grant. The caller still checks the account itself."""
        return self._repo.find_active_access(hash_secret(token), now=self._clock())

    def touch(self, grant_id: str, *, ip: Optional[str]) -> None:
        self._repo.touch_grant(grant_id, now=self._clock(), ip=ip, min_interval_seconds=TOUCH_INTERVAL_SECONDS)

    def revoke_token(self, token: str) -> None:
        """RFC 7009 revocation by the client: the token's whole grant goes,
        since a client revoking a credential is disconnecting."""
        found = self._repo.get_token(hash_secret(token))
        if found is not None:
            self._repo.revoke_grant(found[1].id, now=self._clock(), reason="user")

    # -- grants ------------------------------------------------------------------

    def list_grants(self, user_id: str) -> List[OAuthGrant]:
        return self._repo.list_active_grants(user_id)

    def disconnect(self, user_id: str, grant_id: str) -> bool:
        return self._repo.revoke_grant(grant_id, now=self._clock(), reason="user", user_id=user_id)

    def revoke_all(self, user_id: str, *, reason: RevokeReason = "admin") -> int:
        return self._repo.revoke_user_grants(user_id, now=self._clock(), reason=reason)

    def prune(self) -> int:
        now = self._clock()
        return self._repo.prune(
            now=now, retired_before=now - RETIRED_KEEP, unused_clients_before=now - UNUSED_CLIENT_KEEP
        )
