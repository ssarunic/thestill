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

"""PostgreSQL implementation of ``UserMessageRepository`` (spec #118).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py`` (alembic 0029). ``ensure_pending``'s ``ON CONFLICT DO
NOTHING`` against the ``(user_id, message_key, channel)`` UNIQUE constraint
is the send-once anchor; the leased conditional-claim UPDATE keeps several
workers from double-sending.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

from ..models.user_message import LifecycleFacts, UserMessage
from ..utils.postgres_ext import as_str, connect
from .user_message_repository import UserMessageRepository

logger = get_logger(__name__)

_COLS = "id, user_id, message_key, channel, status, attempts, next_attempt_at, sent_at, last_error, created_at"

_CANDIDATES_SQL = """
SELECT u.id, u.email, u.name, u.created_at,
       s.timezone AS timezone_name,
       COALESCE(s.enabled, false) AS briefing_enabled,
       COALESCE(s.email_enabled, false) AS briefing_email_enabled,
       s.hour_local AS briefing_hour_local,
       (SELECT count(*) FROM podcast_followers f WHERE f.user_id = u.id) AS follow_count,
       EXISTS (SELECT 1 FROM user_episode_inbox i WHERE i.user_id = u.id AND i.state = 'read') AS has_read,
       (EXISTS (SELECT 1 FROM oauth_grants g WHERE g.user_id = u.id AND g.revoked_at IS NULL)
        OR EXISTS (SELECT 1 FROM mcp_tokens t WHERE t.user_id = u.id AND t.revoked_at IS NULL
                     AND (t.expires_at IS NULL OR t.expires_at > %s)))
           AS connector_connected,
       (SELECT max(d.sent_at)
          FROM briefing_deliveries d JOIN user_briefings b ON b.id = d.briefing_id
         WHERE b.user_id = u.id AND d.status = 'sent') AS last_briefing_email_at,
       (SELECT max(COALESCE(m.sent_at, m.created_at)) FROM user_messages m
         WHERE m.user_id = u.id AND m.status IN ('pending','sending','sent')) AS last_message_at,
       ARRAY(SELECT m.message_key FROM user_messages m
              WHERE m.user_id = u.id AND m.channel = %s) AS message_keys
  FROM users u
  LEFT JOIN user_briefing_schedules s ON s.user_id = u.id
 WHERE u.status = 'active'
   AND u.product_tips_email_enabled
   AND u.email <> ''
   AND u.created_at >= %s
   AND u.created_at <= %s
"""


class PostgresUserMessageRepository(UserMessageRepository):
    """PostgreSQL-backed lifecycle message repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres user message repository")

    def candidates(
        self,
        *,
        created_from: datetime,
        created_to: datetime,
        channel: str,
        limit: int,
        user_id: Optional[str] = None,
        after: Optional[Tuple[datetime, str]] = None,
        now: Optional[datetime] = None,
    ) -> List[LifecycleFacts]:
        sql = _CANDIDATES_SQL
        params: list = [now or datetime.now(timezone.utc), channel, created_from, created_to]
        if user_id is not None:
            sql += " AND u.id = %s"
            params.append(user_id)
        if after is not None:
            sql += " AND (u.created_at > %s OR (u.created_at = %s AND u.id > %s))"
            params.extend([after[0], after[0], after[1]])
        sql += " ORDER BY u.created_at, u.id LIMIT %s"
        params.append(limit)
        with connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_facts(row) for row in rows]

    def ensure_pending(self, user_id: str, message_key: str, channel: str, *, now: datetime) -> bool:
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_messages
                    (id, user_id, message_key, channel, status, attempts, next_attempt_at, created_at)
                VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s)
                ON CONFLICT (user_id, message_key, channel) DO NOTHING
                """,
                (str(uuid.uuid4()), user_id, message_key, channel, now, now),
            )
            return cursor.rowcount == 1

    def get(self, message_id: str) -> Optional[UserMessage]:
        with connect(self.dsn) as conn:
            row = conn.execute(f"SELECT {_COLS} FROM user_messages WHERE id = %s", (message_id,)).fetchone()
            return self._row_to_message(row) if row else None

    def due(self, now: datetime, *, limit: int) -> List[UserMessage]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_messages
                 WHERE status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= %s
                 ORDER BY next_attempt_at
                 LIMIT %s
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_message(row) for row in rows]

    def claim(self, message_id: str, *, now: datetime, lease_seconds: int) -> bool:
        lease_until = now + timedelta(seconds=lease_seconds)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE user_messages
                   SET status = 'sending', next_attempt_at = %s, attempts = attempts + 1
                 WHERE id = %s
                   AND status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= %s
                """,
                (lease_until, message_id, now),
            )
            return cursor.rowcount == 1

    def mark_sent(self, message_id: str, *, sent_at: datetime) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'sent', sent_at = %s, next_attempt_at = NULL, last_error = NULL
                 WHERE id = %s
                """,
                (sent_at, message_id),
            )

    def mark_retry(self, message_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'pending', attempts = %s, next_attempt_at = %s, last_error = %s
                 WHERE id = %s
                """,
                (attempts, next_attempt_at, error, message_id),
            )

    def mark_failed(self, message_id: str, *, attempts: int, error: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'failed', attempts = %s, next_attempt_at = NULL, last_error = %s
                 WHERE id = %s
                """,
                (attempts, error, message_id),
            )

    def mark_skipped(self, message_id: str, *, reason: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE user_messages
                   SET status = 'skipped', next_attempt_at = NULL, last_error = %s
                 WHERE id = %s
                """,
                (reason, message_id),
            )

    def status_counts(self) -> Dict[Tuple[str, str], int]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT message_key, status, count(*) AS n FROM user_messages GROUP BY message_key, status"
            ).fetchall()
            return {(row["message_key"], row["status"]): int(row["n"]) for row in rows}

    @staticmethod
    def _row_to_facts(row: dict) -> LifecycleFacts:
        return LifecycleFacts(
            user_id=as_str(row["id"]),
            email=row["email"],
            name=row["name"],
            created_at=row["created_at"],
            timezone_name=row["timezone_name"],
            briefing_enabled=bool(row["briefing_enabled"]),
            briefing_email_enabled=bool(row["briefing_email_enabled"]),
            briefing_hour_local=row["briefing_hour_local"],
            follow_count=int(row["follow_count"] or 0),
            has_read=bool(row["has_read"]),
            connector_connected=bool(row["connector_connected"]),
            last_briefing_email_at=row["last_briefing_email_at"],
            last_message_at=row["last_message_at"],
            message_keys=frozenset(row["message_keys"] or ()),
        )

    @staticmethod
    def _row_to_message(row: dict) -> UserMessage:
        return UserMessage(
            id=as_str(row["id"]),
            user_id=as_str(row["user_id"]),
            message_key=row["message_key"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            sent_at=row["sent_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
        )
