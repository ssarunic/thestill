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

"""PostgreSQL implementation of ``BriefingDeliveryRepository`` (spec #51).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py``. ``ensure_pending``'s ``ON CONFLICT DO NOTHING``
against the ``(briefing_id, channel)`` UNIQUE constraint is the send-once
anchor; the leased conditional-claim UPDATE keeps multi-instance
deployments from double-sending.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from structlog import get_logger

from ..models.briefing_delivery import BriefingDelivery
from ..utils.postgres_ext import as_str, connect
from .briefing_delivery_repository import BriefingDeliveryRepository

logger = get_logger(__name__)

_COLS = "id, briefing_id, channel, status, attempts, next_attempt_at, sent_at, last_error, created_at"


class PostgresBriefingDeliveryRepository(BriefingDeliveryRepository):
    """PostgreSQL-backed briefing delivery repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres briefing delivery repository")

    def ensure_pending(self, briefing_id: str, channel: str, *, now: datetime) -> bool:
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO briefing_deliveries
                    (id, briefing_id, channel, status, attempts, next_attempt_at, created_at)
                VALUES (%s, %s, %s, 'pending', 0, %s, %s)
                ON CONFLICT (briefing_id, channel) DO NOTHING
                """,
                (str(uuid.uuid4()), briefing_id, channel, now, now),
            )
            return cursor.rowcount == 1

    def get_for_briefing(self, briefing_id: str, channel: str) -> Optional[BriefingDelivery]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM briefing_deliveries WHERE briefing_id = %s AND channel = %s",
                (briefing_id, channel),
            ).fetchone()
            return self._row_to_delivery(row) if row else None

    def due(self, now: datetime, *, limit: int) -> List[BriefingDelivery]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM briefing_deliveries
                 WHERE status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= %s
                 ORDER BY next_attempt_at
                 LIMIT %s
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_delivery(row) for row in rows]

    def claim(self, delivery_id: str, *, now: datetime, lease_seconds: int) -> bool:
        # attempts is consumed at claim time, not settle time: a process
        # crash between claim and settle must still burn retry budget, or
        # a send that reproducibly kills the worker re-claims forever.
        lease_until = now + timedelta(seconds=lease_seconds)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'sending', next_attempt_at = %s, attempts = attempts + 1
                 WHERE id = %s
                   AND status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= %s
                """,
                (lease_until, delivery_id, now),
            )
            return cursor.rowcount == 1

    def mark_sent(self, delivery_id: str, *, sent_at: datetime) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'sent', sent_at = %s, next_attempt_at = NULL, last_error = NULL
                 WHERE id = %s
                """,
                (sent_at, delivery_id),
            )

    def mark_retry(self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'pending', attempts = %s, next_attempt_at = %s, last_error = %s
                 WHERE id = %s
                """,
                (attempts, next_attempt_at, error, delivery_id),
            )

    def mark_failed(self, delivery_id: str, *, attempts: int, error: str) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'failed', attempts = %s, next_attempt_at = NULL, last_error = %s
                 WHERE id = %s
                """,
                (attempts, error, delivery_id),
            )

    @staticmethod
    def _row_to_delivery(row: dict) -> BriefingDelivery:
        return BriefingDelivery(
            id=as_str(row["id"]),
            briefing_id=as_str(row["briefing_id"]),
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            next_attempt_at=row["next_attempt_at"],
            sent_at=row["sent_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
        )
