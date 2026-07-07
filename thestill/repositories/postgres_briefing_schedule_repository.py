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

"""PostgreSQL implementation of ``BriefingScheduleRepository`` (spec #50).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py``. The conditional-claim UPDATE is what makes the
scheduler tick multi-instance safe here: only one instance's UPDATE
matches the expected ``next_run_at`` for a given slot.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from structlog import get_logger

from ..models.briefing_schedule import BriefingSchedule
from ..utils.postgres_ext import as_str, connect
from .briefing_schedule_repository import BriefingScheduleRepository

logger = get_logger(__name__)

_COLS = "user_id, frequency, hour_local, weekday, timezone, enabled, next_run_at, created_at, updated_at"


class PostgresBriefingScheduleRepository(BriefingScheduleRepository):
    """PostgreSQL-backed briefing schedule repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres briefing schedule repository")

    def get(self, user_id: str) -> Optional[BriefingSchedule]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM user_briefing_schedules WHERE user_id = %s",
                (user_id,),
            ).fetchone()
            return self._row_to_schedule(row) if row else None

    def upsert(self, schedule: BriefingSchedule) -> BriefingSchedule:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO user_briefing_schedules
                    (user_id, frequency, hour_local, weekday, timezone,
                     enabled, next_run_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    frequency   = excluded.frequency,
                    hour_local  = excluded.hour_local,
                    weekday     = excluded.weekday,
                    timezone    = excluded.timezone,
                    enabled     = excluded.enabled,
                    next_run_at = excluded.next_run_at,
                    updated_at  = excluded.updated_at
                """,
                (
                    schedule.user_id,
                    schedule.frequency.value,
                    schedule.hour_local,
                    schedule.weekday,
                    schedule.timezone_name,
                    schedule.enabled,
                    schedule.next_run_at,
                    schedule.created_at,
                    schedule.updated_at,
                ),
            )
        return schedule

    def due(self, now: datetime, *, limit: int) -> List[BriefingSchedule]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_briefing_schedules
                 WHERE enabled
                   AND next_run_at IS NOT NULL
                   AND next_run_at <= %s
                 ORDER BY next_run_at
                 LIMIT %s
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_schedule(row) for row in rows]

    def claim(self, user_id: str, *, expected_next_run_at: datetime, new_next_run_at: datetime) -> bool:
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE user_briefing_schedules
                   SET next_run_at = %s
                 WHERE user_id = %s
                   AND enabled
                   AND next_run_at = %s
                """,
                (new_next_run_at, user_id, expected_next_run_at),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _row_to_schedule(row: dict) -> BriefingSchedule:
        return BriefingSchedule(
            user_id=as_str(row["user_id"]),
            frequency=row["frequency"],
            hour_local=row["hour_local"],
            weekday=row["weekday"],
            timezone_name=row["timezone"],
            enabled=row["enabled"],
            next_run_at=row["next_run_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
