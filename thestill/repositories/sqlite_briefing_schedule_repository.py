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

"""SQLite implementation of ``BriefingScheduleRepository`` (spec #50)."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from structlog import get_logger

from ..models.briefing_schedule import BriefingSchedule
from ..utils.sqlite_ext import connect
from .briefing_schedule_repository import BriefingScheduleRepository

logger = get_logger(__name__)

_COLS = "user_id, frequency, hour_local, weekday, timezone, enabled, email_enabled, next_run_at, created_at, updated_at"


class SqliteBriefingScheduleRepository(BriefingScheduleRepository):
    """SQLite-based briefing schedule repository.

    Timestamps are stored as tz-aware ISO-8601 strings (``+00:00``), the
    project convention. The due-scan's ``<=`` and the claim's equality both
    compare these strings; ``next_run_at`` is always written via
    ``datetime.isoformat()`` of a UTC datetime, so the format is uniform.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite briefing schedule repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Tuned SQLite connection. See ``thestill.utils.sqlite_ext.connect``."""
        with connect(self.db_path) as conn:
            yield conn

    def get(self, user_id: str) -> Optional[BriefingSchedule]:
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM user_briefing_schedules WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return self._row_to_schedule(row) if row else None

    def upsert(self, schedule: BriefingSchedule) -> BriefingSchedule:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_briefing_schedules
                    (user_id, frequency, hour_local, weekday, timezone,
                     enabled, email_enabled, next_run_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    frequency     = excluded.frequency,
                    hour_local    = excluded.hour_local,
                    weekday       = excluded.weekday,
                    timezone      = excluded.timezone,
                    enabled       = excluded.enabled,
                    email_enabled = excluded.email_enabled,
                    next_run_at   = excluded.next_run_at,
                    updated_at    = excluded.updated_at
                """,
                (
                    schedule.user_id,
                    schedule.frequency.value,
                    schedule.hour_local,
                    schedule.weekday,
                    schedule.timezone_name,
                    int(schedule.enabled),
                    int(schedule.email_enabled),
                    schedule.next_run_at.isoformat() if schedule.next_run_at else None,
                    schedule.created_at.isoformat(),
                    schedule.updated_at.isoformat(),
                ),
            )
        return schedule

    def due(self, now: datetime, *, limit: int) -> List[BriefingSchedule]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_briefing_schedules
                 WHERE enabled = 1
                   AND next_run_at IS NOT NULL
                   AND next_run_at <= ?
                 ORDER BY next_run_at
                 LIMIT ?
                """,
                (now.isoformat(), limit),
            ).fetchall()
            return [self._row_to_schedule(row) for row in rows]

    def claim(self, user_id: str, *, expected_next_run_at: datetime, new_next_run_at: datetime) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE user_briefing_schedules
                   SET next_run_at = ?
                 WHERE user_id = ?
                   AND enabled = 1
                   AND next_run_at = ?
                """,
                (new_next_run_at.isoformat(), user_id, expected_next_run_at.isoformat()),
            )
            return cursor.rowcount == 1

    def set_email_enabled(self, user_id: str, enabled: bool) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE user_briefing_schedules SET email_enabled = ? WHERE user_id = ?",
                (int(enabled), user_id),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_schedule(row: sqlite3.Row) -> BriefingSchedule:
        return BriefingSchedule(
            user_id=row["user_id"],
            frequency=row["frequency"],
            hour_local=row["hour_local"],
            weekday=row["weekday"],
            timezone_name=row["timezone"],
            enabled=bool(row["enabled"]),
            email_enabled=bool(row["email_enabled"]),
            next_run_at=(datetime.fromisoformat(row["next_run_at"]) if row["next_run_at"] else None),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
