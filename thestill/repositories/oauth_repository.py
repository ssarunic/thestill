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
Repository for the OAuth authorization server tables (spec #109).

One repository for all five tables because the operations that must be
atomic cross them: approving a pending request consumes it, keeps or
replaces the grant and issues a code; redeeming a code marks it used and
inserts tokens; rotating a refresh token retires it and inserts its
successors.

Validity is decided in SQL. A code or token is usable only while its grant
is unrevoked, so revoking a grant is one row update that cuts off everything
issued under it. Time is always a parameter, never read inside a backend.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from ..models.oauth import OAuthClientRecord, OAuthCode, OAuthGrant, OAuthPending, OAuthTokenRecord, RevokeReason


class OAuthRepository(ABC):
    """Abstract repository for ``oauth_*`` tables."""

    # -- clients -------------------------------------------------------------

    @abstractmethod
    def get_client(self, client_id: str) -> Optional[OAuthClientRecord]:
        """The stored client, or None."""

    @abstractmethod
    def save_client(self, client: OAuthClientRecord) -> None:
        """Insert or replace the client row (a DCR registration or a CIMD
        fetch). ``created_at`` of an existing row is kept."""

    # -- pending authorization requests ------------------------------------

    @abstractmethod
    def save_pending(self, pending: OAuthPending) -> None:
        """Park an ``/authorize`` request."""

    @abstractmethod
    def get_pending(self, id_hash: str, *, now: datetime) -> Optional[OAuthPending]:
        """The unexpired pending request, or None."""

    @abstractmethod
    def delete_pending(self, id_hash: str) -> bool:
        """Drop a pending request (deny). True when one existed."""

    @abstractmethod
    def approve(
        self,
        id_hash: str,
        *,
        now: datetime,
        user_id: str,
        new_grant_id: str,
        client_name: Optional[str],
        redirect_host: str,
        scopes: Sequence[str],
        code_hash: str,
        code_expires_at: datetime,
    ) -> Optional[Tuple[OAuthGrant, OAuthCode]]:
        """Approve a pending request, atomically.

        Consumes the unexpired pending row (None when it is gone, so two
        approvals of one request cannot both succeed). If the user has an
        active grant for the client with exactly ``scopes``, it is kept;
        any other active grant is revoked with reason ``replaced`` and a
        new grant ``new_grant_id`` is created. A code carrying its own copy
        of ``scopes`` is issued under the grant that ends up active.
        """

    # -- codes ---------------------------------------------------------------

    @abstractmethod
    def get_code(self, code_hash: str) -> Optional[Tuple[OAuthCode, OAuthGrant]]:
        """The code and its grant in any state, so the caller can tell an
        unknown code from a replayed one."""

    @abstractmethod
    def redeem_code(self, code_hash: str, *, now: datetime, tokens: Sequence[OAuthTokenRecord]) -> bool:
        """Mark the code used and insert ``tokens``, atomically. False (and
        nothing written) when the code is used, expired, or its grant is
        revoked."""

    # -- tokens --------------------------------------------------------------

    @abstractmethod
    def find_active_access(self, token_hash: str, *, now: datetime) -> Optional[Tuple[OAuthTokenRecord, OAuthGrant]]:
        """The unexpired, unrevoked access token under an active grant.
        The bearer guard's per-request lookup."""

    @abstractmethod
    def get_token(self, token_hash: str) -> Optional[Tuple[OAuthTokenRecord, OAuthGrant]]:
        """The token and its grant in any state (for refresh reuse detection
        and revocation)."""

    @abstractmethod
    def rotate_refresh(self, old_hash: str, *, now: datetime, tokens: Sequence[OAuthTokenRecord]) -> bool:
        """Retire refresh token ``old_hash`` (``replaced_by_hash`` set to the
        new refresh token's hash) and insert ``tokens``, atomically. False
        (and nothing written) unless the old token is an unexpired,
        unretired, unrevoked refresh token under an active grant."""

    @abstractmethod
    def revoke_token(self, token_hash: str, *, now: datetime) -> bool:
        """Revoke one token. True when an unrevoked one existed."""

    # -- grants --------------------------------------------------------------

    @abstractmethod
    def get_grant(self, grant_id: str) -> Optional[OAuthGrant]:
        """The grant in any state, or None."""

    @abstractmethod
    def list_active_grants(self, user_id: str) -> List[OAuthGrant]:
        """The user's unrevoked grants, newest first."""

    @abstractmethod
    def revoke_grant(
        self, grant_id: str, *, now: datetime, reason: RevokeReason, user_id: Optional[str] = None
    ) -> bool:
        """Revoke one grant (and with it every code and token under it).
        With ``user_id``, only that user's grant matches. True when an
        active grant was revoked."""

    @abstractmethod
    def revoke_user_grants(self, user_id: str, *, now: datetime, reason: RevokeReason) -> int:
        """Revoke every active grant of the user; the count revoked."""

    @abstractmethod
    def touch_grant(self, grant_id: str, *, now: datetime, ip: Optional[str], min_interval_seconds: int) -> bool:
        """Bump ``last_used_at``/``last_used_ip`` unless bumped within
        ``min_interval_seconds``. True when a write happened."""

    # -- housekeeping --------------------------------------------------------

    @abstractmethod
    def prune(self, *, now: datetime, retired_before: datetime, unused_clients_before: datetime) -> int:
        """Delete what can never be used again; the number of rows deleted.

        Expired pending requests; expired codes (used ones are kept until
        then, so a replay is recognised); tokens that are expired; refresh tokens that are expired or were
        retired/revoked before ``retired_before``; grants revoked before
        ``retired_before`` (cascading their codes and tokens); DCR clients
        created before ``unused_clients_before`` that have no grant at all.
        """
