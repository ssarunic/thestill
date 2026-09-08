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
"""SQLite implementation of ``McpTokenRepository`` (spec #78 Phase 2)."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from structlog import get_logger

from ..models.mcp_token import McpToken
from ..utils.sqlite_ext import connect
from .mcp_token_repository import McpTokenRepository

logger = get_logger(__name__)

_COLS = "user_id, token_hash, token_prefix, scopes, created_at, expires_at, last_used_at, last_used_ip, revoked_at"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class SqliteMcpTokenRepository(McpTokenRepository):
    """SQLite-backed MCP token repository.

    Timestamps are tz-aware ISO-8601 strings (``+00:00``), the project
    convention, so the expiry and throttle comparisons are plain string
    comparisons on a uniform format.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite MCP token repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            yield conn

    def get(self, user_id: str) -> Optional[McpToken]:
        with self._get_connection() as conn:
            row = conn.execute(f"SELECT {_COLS} FROM mcp_tokens WHERE user_id = ?", (user_id,)).fetchone()
            return self._row_to_token(row) if row else None

    def upsert(self, token: McpToken) -> McpToken:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO mcp_tokens
                    (user_id, token_hash, token_prefix, scopes, created_at,
                     expires_at, last_used_at, last_used_ip, revoked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
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
                    token.created_at.isoformat(),
                    _iso(token.expires_at),
                    _iso(token.last_used_at),
                    token.last_used_ip,
                    _iso(token.revoked_at),
                ),
            )
        return token

    def find_active(self, token_hash: str, *, now: datetime) -> Optional[McpToken]:
        with self._get_connection() as conn:
            row = conn.execute(
                f"""
                SELECT {_COLS} FROM mcp_tokens
                 WHERE token_hash = ?
                   AND revoked_at IS NULL
                   AND (expires_at IS NULL OR expires_at > ?)
                """,
                (token_hash, now.isoformat()),
            ).fetchone()
            return self._row_to_token(row) if row else None

    def revoke(self, user_id: str, *, revoked_at: datetime) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE mcp_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (revoked_at.isoformat(), user_id),
            )
            return cursor.rowcount == 1

    def touch_last_used(self, user_id: str, *, now: datetime, ip: Optional[str], min_interval_seconds: int) -> bool:
        cutoff = now - timedelta(seconds=min_interval_seconds)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE mcp_tokens
                   SET last_used_at = ?, last_used_ip = ?
                 WHERE user_id = ?
                   AND (last_used_at IS NULL OR last_used_at <= ?)
                """,
                (now.isoformat(), ip, user_id, cutoff.isoformat()),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_token(row: sqlite3.Row) -> McpToken:
        return McpToken(
            user_id=row["user_id"],
            token_hash=row["token_hash"],
            token_prefix=row["token_prefix"],
            scopes=tuple(s for s in row["scopes"].split(",") if s),
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=_dt(row["expires_at"]),
            last_used_at=_dt(row["last_used_at"]),
            last_used_ip=row["last_used_ip"],
            revoked_at=_dt(row["revoked_at"]),
        )
