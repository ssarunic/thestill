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
"""PostgreSQL implementation of ``McpTokenRepository`` (spec #78 Phase 2).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py`` and alembic ``0009_mcp_tokens``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from structlog import get_logger

from ..models.mcp_token import McpToken
from ..utils.postgres_ext import as_str, connect
from .mcp_token_repository import McpTokenRepository

logger = get_logger(__name__)

_COLS = "user_id, token_hash, token_prefix, scopes, created_at, expires_at, last_used_at, last_used_ip, revoked_at"


class PostgresMcpTokenRepository(McpTokenRepository):
    """PostgreSQL-backed MCP token repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres MCP token repository")

    def get(self, user_id: str) -> Optional[McpToken]:
        with connect(self.dsn) as conn:
            row = conn.execute(f"SELECT {_COLS} FROM mcp_tokens WHERE user_id = %s", (user_id,)).fetchone()
            return self._row_to_token(row) if row else None

    def upsert(self, token: McpToken) -> McpToken:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO mcp_tokens
                    (user_id, token_hash, token_prefix, scopes, created_at,
                     expires_at, last_used_at, last_used_ip, revoked_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    token_hash   = excluded.token_hash,
                    token_prefix = excluded.token_prefix,
                    scopes       = excluded.scopes,
                    created_at   = excluded.created_at,
                    expires_at   = excluded.expires_at,
                    last_used_at = excluded.last_used_at,
                    last_used_ip = excluded.last_used_ip,
                    revoked_at   = excluded.revoked_at
                """,
                (
                    token.user_id,
                    token.token_hash,
                    token.token_prefix,
                    ",".join(token.scopes),
                    token.created_at,
                    token.expires_at,
                    token.last_used_at,
                    token.last_used_ip,
                    token.revoked_at,
                ),
            )
        return token

    def find_active(self, token_hash: str, *, now: datetime) -> Optional[McpToken]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_COLS} FROM mcp_tokens
                 WHERE token_hash = %s
                   AND revoked_at IS NULL
                   AND (expires_at IS NULL OR expires_at > %s)
                """,
                (token_hash, now),
            ).fetchone()
            return self._row_to_token(row) if row else None

    def revoke(self, user_id: str, *, revoked_at: datetime) -> bool:
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE mcp_tokens SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
                (revoked_at, user_id),
            )
            return cursor.rowcount == 1

    def touch_last_used(self, user_id: str, *, now: datetime, ip: Optional[str], min_interval_seconds: int) -> bool:
        cutoff = now - timedelta(seconds=min_interval_seconds)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE mcp_tokens
                   SET last_used_at = %s, last_used_ip = %s
                 WHERE user_id = %s
                   AND (last_used_at IS NULL OR last_used_at <= %s)
                """,
                (now, ip, user_id, cutoff),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_token(row) -> McpToken:
        return McpToken(
            user_id=as_str(row["user_id"]),
            token_hash=row["token_hash"],
            token_prefix=row["token_prefix"],
            scopes=tuple(s for s in row["scopes"].split(",") if s),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            last_used_ip=row["last_used_ip"],
            revoked_at=row["revoked_at"],
        )
