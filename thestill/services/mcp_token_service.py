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
"""Per-user remote MCP token lifecycle (spec #78 Phase 2).

Owns the rules the repository must not: plaintext generation and hashing,
the scope policy (``read`` locked on, ``pipeline`` only for admins), TTL
to ``expires_at``, and the derived display state. The plaintext exists
only in the return value of :meth:`create_or_rotate` — it is never
persisted or logged.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Iterable, NamedTuple, Optional, Tuple

from structlog import get_logger

from ..models.mcp_token import ALL_SCOPES, SCOPE_PIPELINE, SCOPE_READ, McpToken
from ..repositories.mcp_token_repository import McpTokenRepository
from ..utils.datetime_utils import now_utc

logger = get_logger(__name__)

# How long before expiry the Settings card starts warning.
EXPIRING_SOON = timedelta(days=14)
# Last-use writes are throttled to one per token per this many seconds.
TOUCH_INTERVAL_SECONDS = 60
# Kept in the clear for the masked display: "…/mcp/abcdef••••".
PREFIX_LENGTH = 6


class McpTokenMint(NamedTuple):
    """What a create/rotate returns — the plaintext appears here and nowhere else."""

    token: str
    scopes: Tuple[str, ...]
    expires_at: Optional[datetime]


class McpTokenService:
    """Mint, resolve, revoke and describe per-user MCP tokens."""

    def __init__(self, repository: McpTokenRepository, *, ttl_days: int):
        if ttl_days < 0:
            raise ValueError(f"ttl_days must be >= 0 (0 = never expires); got {ttl_days}")
        self._repo = repository
        self._ttl_days = ttl_days

    @staticmethod
    def hash_token(plaintext: str) -> str:
        return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_scopes(requested: Iterable[str], *, is_admin: bool) -> Tuple[str, ...]:
        """Apply the scope policy: unknown scopes ignored, ``read`` always
        present, ``pipeline`` silently dropped for non-admins (not an
        error — the spec wants the card to just not grant it)."""
        wanted = {s for s in requested if s in ALL_SCOPES}
        wanted.add(SCOPE_READ)
        if not is_admin:
            wanted.discard(SCOPE_PIPELINE)
        return tuple(s for s in ALL_SCOPES if s in wanted)

    def create_or_rotate(
        self,
        user_id: str,
        *,
        requested_scopes: Iterable[str],
        is_admin: bool,
        now: Optional[datetime] = None,
    ) -> McpTokenMint:
        now = now or now_utc()
        plaintext = secrets.token_hex(32)
        scopes = self.normalize_scopes(requested_scopes, is_admin=is_admin)
        expires_at = now + timedelta(days=self._ttl_days) if self._ttl_days != 0 else None
        self._repo.upsert(
            McpToken(
                user_id=user_id,
                token_hash=self.hash_token(plaintext),
                token_prefix=plaintext[:PREFIX_LENGTH],
                scopes=scopes,
                created_at=now,
                expires_at=expires_at,
            )
        )
        logger.info("mcp_token_minted", user_id=user_id, scopes=list(scopes), expires_at=expires_at)
        return McpTokenMint(token=plaintext, scopes=scopes, expires_at=expires_at)

    def resolve_active(self, plaintext: str, *, now: Optional[datetime] = None) -> Optional[McpToken]:
        """The guard's lookup: hash the path segment, find a live row."""
        return self._repo.find_active(self.hash_token(plaintext), now=now or now_utc())

    def touch_last_used(self, user_id: str, *, ip: Optional[str], now: Optional[datetime] = None) -> None:
        self._repo.touch_last_used(user_id, now=now or now_utc(), ip=ip, min_interval_seconds=TOUCH_INTERVAL_SECONDS)

    def get(self, user_id: str) -> Optional[McpToken]:
        return self._repo.get(user_id)

    def revoke(self, user_id: str, *, now: Optional[datetime] = None) -> bool:
        revoked = self._repo.revoke(user_id, revoked_at=now or now_utc())
        if revoked:
            logger.info("mcp_token_revoked", user_id=user_id)
        return revoked

    @staticmethod
    def state_of(token: McpToken, *, now: Optional[datetime] = None) -> str:
        """Display state: active | expiring | expired | revoked."""
        now = now or now_utc()
        if token.revoked_at is not None:
            return "revoked"
        if token.expires_at is not None:
            if token.expires_at <= now:
                return "expired"
            if token.expires_at - now <= EXPIRING_SOON:
                return "expiring"
        return "active"
