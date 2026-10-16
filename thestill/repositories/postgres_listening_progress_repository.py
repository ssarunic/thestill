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
"""PostgreSQL implementation of ``ListeningProgressRepository`` (spec #90).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py`` and alembic ``0016_user_episode_progress``.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from structlog import get_logger

from ..models.listening import ListeningProgress
from ..utils.postgres_ext import as_str, connect
from .listening_progress_repository import ListeningProgressRepository

logger = get_logger(__name__)

_COLS = "user_id, episode_id, position_seconds, duration_seconds, completed_at, stopped_at, session_id, seq, updated_at"


def _is_uuid(value: str) -> bool:
    # Ids reach this repository straight from URL paths; a malformed one
    # would raise on the ::uuid cast instead of matching nothing.
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


class PostgresListeningProgressRepository(ListeningProgressRepository):
    """PostgreSQL-backed listening progress repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres listening progress repository")

    def get(self, user_id: str, episode_id: str) -> Optional[ListeningProgress]:
        if not (_is_uuid(user_id) and _is_uuid(episode_id)):
            return None
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM user_episode_progress WHERE user_id = %s AND episode_id = %s",
                (user_id, episode_id),
            ).fetchone()
            return self._row_to_progress(row) if row else None

    def upsert_if_newer(self, row: ListeningProgress, *, force: bool = False) -> bool:
        if not (_is_uuid(row.user_id) and _is_uuid(row.episode_id)):
            return False
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_episode_progress
                    (user_id, episode_id, position_seconds, duration_seconds,
                     completed_at, stopped_at, session_id, seq, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, episode_id) DO UPDATE SET
                    position_seconds = excluded.position_seconds,
                    duration_seconds = COALESCE(excluded.duration_seconds,
                                                user_episode_progress.duration_seconds),
                    completed_at     = excluded.completed_at,
                    stopped_at       = excluded.stopped_at,
                    session_id       = excluded.session_id,
                    seq              = excluded.seq,
                    updated_at       = excluded.updated_at
                 WHERE %s
                    OR excluded.session_id <> user_episode_progress.session_id
                    OR excluded.seq > user_episode_progress.seq
                """,
                (
                    row.user_id,
                    row.episode_id,
                    row.position_seconds,
                    row.duration_seconds,
                    row.completed_at,
                    row.stopped_at,
                    row.session_id,
                    row.seq,
                    row.updated_at,
                    force,
                ),
            )
            return cursor.rowcount == 1

    def list_unfinished(self, user_id: str, *, limit: int, exclude_stopped: bool) -> List[ListeningProgress]:
        if not _is_uuid(user_id):
            return []
        stopped_clause = "AND stopped_at IS NULL" if exclude_stopped else ""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS} FROM user_episode_progress
                 WHERE user_id = %s
                   AND completed_at IS NULL
                   AND position_seconds > 0
                   {stopped_clause}
                 ORDER BY updated_at DESC
                 LIMIT %s
                """,
                (user_id, limit),
            ).fetchall()
            return [self._row_to_progress(r) for r in rows]

    @staticmethod
    def _row_to_progress(row) -> ListeningProgress:
        return ListeningProgress(
            user_id=as_str(row["user_id"]),
            episode_id=as_str(row["episode_id"]),
            position_seconds=row["position_seconds"],
            duration_seconds=row["duration_seconds"],
            completed_at=row["completed_at"],
            stopped_at=row["stopped_at"],
            session_id=row["session_id"],
            seq=row["seq"],
            updated_at=row["updated_at"],
        )
