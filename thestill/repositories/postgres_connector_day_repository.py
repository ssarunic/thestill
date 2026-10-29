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

"""PostgreSQL implementation of ``ConnectorDayRepository`` (spec #111).

Schema in ``postgres_schema.py`` and alembic ``0026_connector_days``.
"""

from __future__ import annotations

from datetime import datetime

from structlog import get_logger

from ..utils.ids import is_uuid
from ..utils.postgres_ext import connect
from .connector_day_repository import ConnectorDayRepository
from .postgres_user_repository import consents_to_measurement

logger = get_logger(__name__)


class PostgresConnectorDayRepository(ConnectorDayRepository):
    """PostgreSQL-backed connector-day store."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres connector day repository")

    def record_day(self, user_id: str, at: datetime) -> bool:
        if not is_uuid(user_id):
            return False
        with connect(self.dsn) as conn:
            if not consents_to_measurement(conn, user_id):
                return False
            # The reader's local day, the same bucketing the Scorecard uses
            # for every other value fact (spec #111 §2).
            cursor = conn.execute(
                """
                INSERT INTO user_connector_days (user_id, day, first_at)
                SELECT u.id, (%(at)s::timestamptz AT TIME ZONE COALESCE(s.timezone, 'UTC'))::date, %(at)s
                  FROM users u LEFT JOIN user_briefing_schedules s ON s.user_id = u.id
                 WHERE u.id = %(user_id)s
                ON CONFLICT (user_id, day) DO NOTHING
                """,
                {"at": at, "user_id": user_id},
            )
            return cursor.rowcount == 1
