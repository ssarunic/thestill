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

"""SQLite implementation of ``BriefingRepository`` (spec #36)."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from structlog import get_logger

from ..models.briefing import Briefing
from .briefing_repository import BriefingRepository

logger = get_logger(__name__)


class SqliteBriefingRepository(BriefingRepository):
    """SQLite-based per-user briefing repository."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite briefing repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def insert(self, briefing: Briefing) -> Briefing:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO user_briefings
                    (id, user_id, cursor_from, cursor_to, episode_count,
                     script_path, audio_path, created_at, listened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    briefing.id,
                    briefing.user_id,
                    briefing.cursor_from.isoformat(),
                    briefing.cursor_to.isoformat(),
                    briefing.episode_count,
                    briefing.script_path,
                    briefing.audio_path,
                    briefing.created_at.isoformat(),
                    briefing.listened_at.isoformat() if briefing.listened_at else None,
                ),
            )
        return briefing

    def get(self, briefing_id: str) -> Optional[Briefing]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, cursor_from, cursor_to, episode_count,
                       script_path, audio_path, created_at, listened_at
                  FROM user_briefings
                 WHERE id = ?
                """,
                (briefing_id,),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    def latest_for_user(self, user_id: str) -> Optional[Briefing]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, cursor_from, cursor_to, episode_count,
                       script_path, audio_path, created_at, listened_at
                  FROM user_briefings
                 WHERE user_id = ?
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    def update_listened_at(self, briefing_id: str, listened_at: datetime) -> Optional[Briefing]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                UPDATE user_briefings
                   SET listened_at = ?
                 WHERE id = ?
                RETURNING id, user_id, cursor_from, cursor_to, episode_count,
                          script_path, audio_path, created_at, listened_at
                """,
                (listened_at.isoformat(), briefing_id),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    @staticmethod
    def _row_to_briefing(row: sqlite3.Row) -> Briefing:
        return Briefing(
            id=row["id"],
            user_id=row["user_id"],
            cursor_from=datetime.fromisoformat(row["cursor_from"]),
            cursor_to=datetime.fromisoformat(row["cursor_to"]),
            episode_count=row["episode_count"],
            script_path=row["script_path"],
            audio_path=row["audio_path"],
            created_at=datetime.fromisoformat(row["created_at"]),
            listened_at=(datetime.fromisoformat(row["listened_at"]) if row["listened_at"] else None),
        )
