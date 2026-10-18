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
"""SQLite implementation of ``ListeningProgressRepository`` (spec #90)."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from structlog import get_logger

from ..models.listening import ListeningProgress
from ..utils.sqlite_ext import connect
from .listening_progress_repository import ListeningProgressRepository

logger = get_logger(__name__)

_COLS = (
    "user_id, episode_id, position_seconds, duration_seconds, completed_at, stopped_at, session_id, seq, updated_at,"
    " listened_at"
)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class SqliteListeningProgressRepository(ListeningProgressRepository):
    """SQLite-backed listening progress repository.

    Timestamps are tz-aware ISO-8601 strings (``+00:00``), the project
    convention, so ``ORDER BY updated_at`` is a plain string sort.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite listening progress repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            yield conn

    def get(self, user_id: str, episode_id: str) -> Optional[ListeningProgress]:
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM user_episode_progress WHERE user_id = ? AND episode_id = ?",
                (user_id, episode_id),
            ).fetchone()
            return self._row_to_progress(row) if row else None

    def upsert_if_newer(self, row: ListeningProgress, *, force: bool = False) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_episode_progress
                    (user_id, episode_id, position_seconds, duration_seconds,
                     completed_at, stopped_at, session_id, seq, updated_at, listened_at)
                -- listened_at only for a user whose switch is on; one
                -- statement, so it can't interleave with the opt-out's erase.
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        CASE WHEN EXISTS (SELECT 1 FROM users WHERE id = ? AND usage_analytics_enabled = 1)
                             THEN ? END)
                ON CONFLICT(user_id, episode_id) DO UPDATE SET
                    position_seconds = excluded.position_seconds,
                    duration_seconds = COALESCE(excluded.duration_seconds,
                                                user_episode_progress.duration_seconds),
                    completed_at     = excluded.completed_at,
                    stopped_at       = excluded.stopped_at,
                    session_id       = excluded.session_id,
                    seq              = excluded.seq,
                    updated_at       = excluded.updated_at,
                    -- Only moves forward, so a write planned from a stale
                    -- read never rewinds it (spec #103).
                    listened_at      = COALESCE(max(excluded.listened_at, user_episode_progress.listened_at),
                                         excluded.listened_at, user_episode_progress.listened_at)
                 WHERE ?
                    OR excluded.session_id <> user_episode_progress.session_id
                    OR excluded.seq > user_episode_progress.seq
                """,
                (
                    row.user_id,
                    row.episode_id,
                    row.position_seconds,
                    row.duration_seconds,
                    _iso(row.completed_at),
                    _iso(row.stopped_at),
                    row.session_id,
                    row.seq,
                    row.updated_at.isoformat(),
                    row.user_id,
                    _iso(row.listened_at),
                    1 if force else 0,
                ),
            )
            return cursor.rowcount == 1

    def list_unfinished(
        self,
        user_id: str,
        *,
        limit: int,
        exclude_stopped: bool,
        updated_since: Optional[datetime] = None,
    ) -> List[ListeningProgress]:
        stopped_clause = "AND stopped_at IS NULL" if exclude_stopped else ""
        since_clause = "AND updated_at >= ?" if updated_since is not None else ""
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM user_episode_progress
                 WHERE user_id = ?
                   AND completed_at IS NULL
                   AND position_seconds > 0
                   {stopped_clause}
                   {since_clause}
                 ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (user_id, *([updated_since.isoformat()] if updated_since is not None else []), limit),
            ).fetchall()
            return [self._row_to_progress(r) for r in rows]

    @staticmethod
    def _row_to_progress(row: sqlite3.Row) -> ListeningProgress:
        return ListeningProgress(
            user_id=row["user_id"],
            episode_id=row["episode_id"],
            position_seconds=row["position_seconds"],
            duration_seconds=row["duration_seconds"],
            completed_at=_dt(row["completed_at"]),
            stopped_at=_dt(row["stopped_at"]),
            session_id=row["session_id"],
            seq=row["seq"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            listened_at=_dt(row["listened_at"]),
        )
