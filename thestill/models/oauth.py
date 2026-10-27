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
OAuth authorization server rows for the remote MCP server (spec #109).

Five tables, one aggregate: a *client* (registered through DCR or a cached
Client ID Metadata Document) asks for access; the reader approves a
*pending* request, which creates or replaces their *grant* for that client
and issues a single-use *code*; the code is exchanged for *tokens*.

Every credential (code, access token, refresh token) carries its own scope
set. A grant's ``scopes`` is a display copy for Settings; nothing reads it to
authorize a request. Revoking a grant invalidates every code and token under
it, because each lookup joins on the grant being active.

Secrets (pending ids, codes, tokens) are stored as SHA-256 hashes only.
"""

from datetime import datetime
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, Field

# Requested alongside the MCP scopes to get a refresh token. Never an MCP
# scope: ``McpTokenService.normalize_scopes`` would drop it, so it is
# handled separately and never reaches ``STATE_SCOPES``.
SCOPE_OFFLINE_ACCESS = "offline_access"

ClientSource = Literal["cimd", "dcr"]
TokenKind = Literal["access", "refresh"]
# Why a grant stopped working: the reader disconnected it, an admin
# suspended/banned/logged out the account, a re-approval with different
# scopes replaced it, or a retired refresh token or used code came back.
RevokeReason = Literal["user", "admin", "replaced", "reuse"]


class OAuthClientRecord(BaseModel):
    """A registered (DCR) or fetched (CIMD) client.

    ``metadata`` is the SDK's ``OAuthClientInformationFull`` as JSON, the
    document as the client stated it; the scope policy is applied when the
    record is turned back into an SDK client, not here.
    """

    client_id: str
    source: ClientSource
    client_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    fetched_at: Optional[datetime] = None
    # CIMD only: refetch the document after this (its max-age, capped).
    fresh_until: Optional[datetime] = None


class OAuthPending(BaseModel):
    """An ``/authorize`` request parked until the reader decides."""

    id_hash: str
    client_id: str
    redirect_uri: str
    redirect_uri_explicit: bool
    state: Optional[str] = None
    scopes: Tuple[str, ...]
    code_challenge: str
    resource: Optional[str] = None
    created_at: datetime
    expires_at: datetime


class OAuthGrant(BaseModel):
    """A reader's consent to one client. At most one active per (user, client)."""

    id: str
    user_id: str
    client_id: str
    client_name: Optional[str] = None
    redirect_host: str
    scopes: Tuple[str, ...]
    created_at: datetime
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    revoked_at: Optional[datetime] = None
    revoked_reason: Optional[RevokeReason] = None


class OAuthCode(BaseModel):
    """A single-use authorization code with its own copy of the approved scopes."""

    code_hash: str
    grant_id: str
    client_id: str
    scopes: Tuple[str, ...]
    redirect_uri: str
    redirect_uri_explicit: bool
    code_challenge: str
    resource: Optional[str] = None
    expires_at: datetime
    used_at: Optional[datetime] = None


class OAuthTokenRecord(BaseModel):
    """An access or refresh token. ``replaced_by_hash`` marks a rotated refresh token."""

    token_hash: str
    grant_id: str
    client_id: str
    kind: TokenKind
    scopes: Tuple[str, ...]
    resource: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    replaced_by_hash: Optional[str] = None
    revoked_at: Optional[datetime] = None
