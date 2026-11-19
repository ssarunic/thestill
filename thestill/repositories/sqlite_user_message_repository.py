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

"""SQLite implementation of ``UserMessageRepository`` (spec #118)."""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from structlog import get_logger

from ..models.user_message import LifecycleFacts, UserMessage
from ..utils.sqlite_ext import connect
from .user_message_repository import UserMessageRepository

logger = get_logger(__name__)

_COLS = "id, user_id, message_key, channel, status, attempts, next_attempt_at, sent_at, last_error, created_at"

# One row per candidate; every fact is a correlated subquery on an indexed
# user_id column, and the candidate set is new signups only (days, not the
# whole user table).
_CANDIDATES_SQL = """
SELECT u.id, u.email, u.name, u.created_at,
       s.timezone AS timezone_name,
       COALESCE(s.enabled, 0) AS briefing_enabled,
       COALESCE(s.email_enabled, 0) AS briefing_email_enabled,
       s.hour_local AS briefing_hour_local,
       (SELECT COUNT(*) FROM podcast_followers f WHERE f.user_id = u.id) AS follow_count,
       EXISTS (SELECT 1 FROM user_episode_inbox i WHERE i.user_id = u.id AND i.state = 'read') AS has_read,
       (EXISTS (SELECT 1 FROM oauth_grants g WHERE g.user_id = u.id AND g.revoked_at IS NULL)
        OR EXISTS (SELECT 1 FROM mcp_tokens t WHERE t.user_id = u.id AND t.revoked_at IS NULL))
           AS connector_connected,
       (SELECT MAX(d.sent_at)
          FROM briefing_deliveries d JOIN user_briefings b ON b.id = d.briefing_id
         WHERE b.user_id = u.id AND d.status = 'sent') AS last_briefing_email_at,
       (SELECT MAX(COALESCE(m.sent_at, m.created_at)) FROM user_messages m
         WHERE m.user_id = u.id AND m.status IN ('pending','sending','sent')) AS last_message_at,
       (SELECT GROUP_CONCAT(m.message_key, ',') FROM user_messages m
         WHERE m.user_id = u.id AND m.channel = ?) AS message_keys
  FROM users u
  LEFT JOIN user_briefing_schedules s ON s.user_id = u.id
 WHERE u.status = 'active'
   AND u.product_tips_email_enabled = 1
   AND u.email <> ''
   AND u.created_at >= ?
   AND u.created_at <= ?
"""


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class SqliteUserMessageRepository(UserMessageRepository):
    """SQLite-based lifecycle message repository.

    Timestamps are tz-aware ISO-8601 strings (``+00:00``), the project
    convention; the due-scan and the signup window compare these strings,
    so every write goes through ``datetime.isoformat()`` of a UTC datetime.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite user message repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            yield conn

    def candidates(
        self,
        *,
        created_from: datetime,
        created_to: datetime,
        channel: str,
        limit: int,
        user_id: Optional[str] = None,
        after: Optional[Tuple[datetime, str]] = None,
    ) -> List[LifecycleFacts]:
        sql = _CANDIDATES_SQL
        params: list = [channel, created_from.isoformat(), created_to.isoformat()]
        if user_id is not None:
            sql += " AND u.id = ?"
            params.append(user_id)
        if after is not None:
            sql += " AND (u.created_at > ? OR (u.created_at = ? AND u.id > ?))"
            params.extend([after[0].isoformat(), after[0].isoformat(), after[1]])
        sql += " ORDER BY u.created_at, u.id LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_facts(row) for row in rows]

    def ensure_pending(self, user_id: str, message_key: str, channel: str, *, now: datetime) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_messages
                    (id, user_id, message_key, channel, status, attempts, next_attempt_at, created_at)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT (user_id, message_key, channel) DO NOTHING
                """,
                (str(uuid.uuid4()), user_id, message_key, channel, now.isoformat(), now.isoformat()),
            )
            return cursor.rowcount == 1

    def get(self, message_id: str) -> Optional[UserMessage]:
        with self._get_connection() as conn:
            row = conn.execute(f"SELECT {_COLS} FROM user_messages WHERE id = ?", (message_id,)).fetchone()
            return self._row_to_message(row) if row else None

    def due(self, now: datetime, *, limit: int) -> List[UserMessage]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_messages
                 WHERE status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= ?
                 ORDER BY next_attempt_at
                 LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
            return [self._row_to_message(row) for row in rows]

    def claim(self, message_id: str, *, now: datetime, lease_seconds: int) -> bool:
        # attempts is consumed at claim time, so a crash between claim and
        # settle still burns retry budget (same rule as spec #51).
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE user_messages
                   SET status = 'sending', next_attempt_at = ?, attempts = attempts + 1
                 WHERE id = ?
                   AND status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= ?
                """,
                (lease_until.isoformat(), message_id, now.isoformat()),
            )
            return cursor.rowcount == 1

    def mark_sent(self, message_id: str, *, sent_at: datetime) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'sent', sent_at = ?, next_attempt_at = NULL, last_error = NULL
                 WHERE id = ?
                """,
                (sent_at.isoformat(), message_id),
            )

    def mark_retry(self, message_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'pending', attempts = ?, next_attempt_at = ?, last_error = ?
                 WHERE id = ?
                """,
                (attempts, next_attempt_at.isoformat(), error, message_id),
            )

    def mark_failed(self, message_id: str, *, attempts: int, error: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'failed', attempts = ?, next_attempt_at = NULL, last_error = ?
                 WHERE id = ?
                """,
                (attempts, error, message_id),
            )

    def mark_skipped(self, message_id: str, *, reason: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'skipped', next_attempt_at = NULL, last_error = ?
                 WHERE id = ?
                """,
                (reason, message_id),
            )

    def status_counts(self) -> Dict[Tuple[str, str], int]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT message_key, status, COUNT(*) AS n FROM user_messages GROUP BY message_key, status"
            ).fetchall()
            return {(row["message_key"], row["status"]): row["n"] for row in rows}

    @staticmethod
    def _row_to_facts(row: sqlite3.Row) -> LifecycleFacts:
        keys = row["message_keys"]
        return LifecycleFacts(
            user_id=row["id"],
            email=row["email"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            timezone_name=row["timezone_name"],
            briefing_enabled=bool(row["briefing_enabled"]),
            briefing_email_enabled=bool(row["briefing_email_enabled"]),
            briefing_hour_local=row["briefing_hour_local"],
            follow_count=int(row["follow_count"] or 0),
            has_read=bool(row["has_read"]),
            connector_connected=bool(row["connector_connected"]),
            last_briefing_email_at=_dt(row["last_briefing_email_at"]),
            last_message_at=_dt(row["last_message_at"]),
            message_keys=frozenset(keys.split(",")) if keys else frozenset(),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> UserMessage:
        return UserMessage(
            id=row["id"],
            user_id=row["user_id"],
            message_key=row["message_key"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            next_attempt_at=_dt(row["next_attempt_at"]),
            sent_at=_dt(row["sent_at"]),
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
