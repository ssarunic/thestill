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

"""SQLite implementation of ``ConnectorDayRepository`` (spec #111).

SQLite installs have no analytics; the table is kept so both backends
store the same facts (FM-6). SQLite has no timezone database, so the day
is the UTC date: nothing on SQLite reads it.
"""

from datetime import datetime, timezone
from pathlib import Path

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .connector_day_repository import ConnectorDayRepository

logger = get_logger(__name__)


class SqliteConnectorDayRepository(ConnectorDayRepository):
    """SQLite-backed connector-day store."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite connector day repository", db_path=str(self.db_path))

    def record_day(self, user_id: str, at: datetime) -> bool:
        day = at.astimezone(timezone.utc).date().isoformat()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_connector_days (user_id, day)
                SELECT id, ? FROM users WHERE id = ? AND usage_analytics_enabled = 1
                ON CONFLICT (user_id, day) DO NOTHING
                """,
                (day, user_id),
            )
            return cursor.rowcount == 1
