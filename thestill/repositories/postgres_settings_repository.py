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

"""PostgreSQL implementation of the spec #104 settings repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from psycopg.types.json import Jsonb

from ..utils.postgres_ext import as_str, connect
from .settings_repository import SettingsRepository, StoredSetting


class PostgresSettingsRepository(SettingsRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn

    def get_all(self) -> Dict[str, StoredSetting]:
        with connect(self.dsn) as conn:
            rows = conn.execute("SELECT key, value, updated_at, updated_by FROM app_settings").fetchall()
        return {
            row["key"]: StoredSetting(
                key=row["key"],
                value=row["value"],
                updated_at=row["updated_at"],
                updated_by=as_str(row["updated_by"]),
            )
            for row in rows
        }

    def upsert(self, key: str, value: Any, *, updated_by: Optional[str], updated_at: datetime) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value, updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by
                """,
                (key, Jsonb(value), updated_at, updated_by),
            )

    def delete(self, key: str) -> bool:
        with connect(self.dsn) as conn:
            return conn.execute("DELETE FROM app_settings WHERE key = %s", (key,)).rowcount > 0
