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

"""SQLite implementation of ``BriefingDeliveryRepository`` (spec #51)."""

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, List, Optional

from structlog import get_logger

from ..models.briefing_delivery import BriefingDelivery
from ..utils.sqlite_ext import connect
from .briefing_delivery_repository import BriefingDeliveryRepository

logger = get_logger(__name__)

_COLS = "id, briefing_id, channel, status, attempts, next_attempt_at, sent_at, last_error, created_at"


class SqliteBriefingDeliveryRepository(BriefingDeliveryRepository):
    """SQLite-based briefing delivery repository.

    Timestamps are tz-aware ISO-8601 strings (``+00:00``), the project
    convention; the due-scan's ``<=`` compares these strings, so every
    write goes through ``datetime.isoformat()`` of a UTC datetime.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite briefing delivery repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Tuned SQLite connection. See ``thestill.utils.sqlite_ext.connect``."""
        with connect(self.db_path) as conn:
            yield conn

    def ensure_pending(self, briefing_id: str, channel: str, *, now: datetime) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO briefing_deliveries
                    (id, briefing_id, channel, status, attempts, next_attempt_at, created_at)
                VALUES (?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT (briefing_id, channel) DO NOTHING
                """,
                (str(uuid.uuid4()), briefing_id, channel, now.isoformat(), now.isoformat()),
            )
            return cursor.rowcount == 1

    def get_for_briefing(self, briefing_id: str, channel: str) -> Optional[BriefingDelivery]:
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM briefing_deliveries WHERE briefing_id = ? AND channel = ?",
                (briefing_id, channel),
            ).fetchone()
            return self._row_to_delivery(row) if row else None

    def due(self, now: datetime, *, limit: int) -> List[BriefingDelivery]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM briefing_deliveries
                 WHERE status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= ?
                 ORDER BY next_attempt_at
                 LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
            return [self._row_to_delivery(row) for row in rows]

    def claim(self, delivery_id: str, *, now: datetime, lease_seconds: int) -> bool:
        # attempts is consumed at claim time, not settle time: a process
        # crash between claim and settle must still burn retry budget, or
        # a send that reproducibly kills the worker re-claims forever.
        lease_until = now + timedelta(seconds=lease_seconds)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'sending', next_attempt_at = ?, attempts = attempts + 1
                 WHERE id = ?
                   AND status IN ('pending','sending')
                   AND next_attempt_at IS NOT NULL
                   AND next_attempt_at <= ?
                """,
                (lease_until.isoformat(), delivery_id, now.isoformat()),
            )
            return cursor.rowcount == 1

    def mark_sent(self, delivery_id: str, *, sent_at: datetime) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'sent', sent_at = ?, next_attempt_at = NULL, last_error = NULL
                 WHERE id = ?
                """,
                (sent_at.isoformat(), delivery_id),
            )

    def mark_retry(self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'pending', attempts = ?, next_attempt_at = ?, last_error = ?
                 WHERE id = ?
                """,
                (attempts, next_attempt_at.isoformat(), error, delivery_id),
            )

    def mark_failed(self, delivery_id: str, *, attempts: int, error: str) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE briefing_deliveries
                   SET status = 'failed', attempts = ?, next_attempt_at = NULL, last_error = ?
                 WHERE id = ?
                """,
                (attempts, error, delivery_id),
            )

    @staticmethod
    def _row_to_delivery(row: sqlite3.Row) -> BriefingDelivery:
        return BriefingDelivery(
            id=row["id"],
            briefing_id=row["briefing_id"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            next_attempt_at=(datetime.fromisoformat(row["next_attempt_at"]) if row["next_attempt_at"] else None),
            sent_at=(datetime.fromisoformat(row["sent_at"]) if row["sent_at"] else None),
            last_error=row["last_error"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
