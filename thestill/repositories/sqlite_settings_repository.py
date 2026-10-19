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

"""SQLite implementation of the spec #104 settings repository."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..utils.sqlite_ext import connect
from .settings_repository import SettingsRepository, StoredSetting


class SqliteSettingsRepository(SettingsRepository):
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def get_all(self) -> Dict[str, StoredSetting]:
        with connect(self.db_path) as conn:
            rows = conn.execute("SELECT key, value, updated_at, updated_by FROM app_settings").fetchall()
        return {
            row["key"]: StoredSetting(
                key=row["key"],
                value=json.loads(row["value"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                updated_by=row["updated_by"],
            )
            for row in rows
        }

    def upsert(self, key: str, value: Any, *, updated_by: Optional[str], updated_at: datetime) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by
                """,
                (key, json.dumps(value), updated_at.isoformat(), updated_by),
            )

    def modify(
        self,
        key: str,
        change: Callable[[Optional[Any]], Any],
        *,
        updated_by: Optional[str],
        updated_at: datetime,
    ) -> Any:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")  # serialises the read and the write
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            value = change(json.loads(row["value"]) if row else None)
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by
                """,
                (key, json.dumps(value), updated_at.isoformat(), updated_by),
            )
        return value

    def delete(self, key: str) -> bool:
        with connect(self.db_path) as conn:
            return conn.execute("DELETE FROM app_settings WHERE key = ?", (key,)).rowcount > 0
