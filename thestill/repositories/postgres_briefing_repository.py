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

"""PostgreSQL implementation of ``BriefingRepository`` (spec #44).

Port of ``SqliteBriefingRepository`` following the ``utils.postgres_ext``
conventions: native ``uuid`` ids, ``timestamptz`` datetimes (no isoformat
round-trips), ``%s`` placeholders. Schema in ``postgres_schema.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from structlog import get_logger

from ..core.queue_manager import USER_CHAIN_STAGE_VALUES
from ..models.briefing import Briefing
from ..utils.postgres_ext import as_str, connect
from .briefing_repository import BriefingRepository

logger = get_logger(__name__)

# Values come from our own enum, never user input — safe to inline. The
# wait-set tracks only the user chain (download → summarize); entity/corpus
# post-processing never holds a briefing (spec #55, decision 2026-07-10).
_USER_CHAIN_SQL = ", ".join(f"'{v}'" for v in USER_CHAIN_STAGE_VALUES)

_COLS = "id, user_id, cursor_from, cursor_to, episode_count, script_path, audio_path, created_at, listened_at"


class PostgresBriefingRepository(BriefingRepository):
    """PostgreSQL-backed per-user briefing repository."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres briefing repository")

    def insert(self, briefing: Briefing) -> Briefing:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO user_briefings
                    (id, user_id, cursor_from, cursor_to, episode_count,
                     script_path, audio_path, created_at, listened_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    briefing.id,
                    briefing.user_id,
                    briefing.cursor_from,
                    briefing.cursor_to,
                    briefing.episode_count,
                    briefing.script_path,
                    briefing.audio_path,
                    briefing.created_at,
                    briefing.listened_at,
                ),
            )
        return briefing

    def get(self, briefing_id: str) -> Optional[Briefing]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM user_briefings WHERE id = %s",
                (briefing_id,),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    def latest_for_user(self, user_id: str) -> Optional[Briefing]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_briefings
                 WHERE user_id = %s
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    def count_pending_for_user(
        self,
        user_id: str,
        *,
        since: datetime,
        cutoff: datetime,
    ) -> int:
        """Return the spec #55 wait-set size for ``user_id``."""
        # Two guards keep the gate honest about what can still *arrive*:
        # the stage filter ignores post-summarize entity/corpus work, and
        # ``published_at IS NULL`` ignores already-published episodes whose
        # user-chain re-runs would otherwise stall a briefing for content
        # that will never (re-)deliver.
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT e.id) AS n
                  FROM tasks t
                  JOIN episodes e
                    ON e.id = t.episode_id
                  JOIN podcast_followers pf
                    ON pf.podcast_id = e.podcast_id
                   AND pf.user_id = %s
                 WHERE e.pub_date >= %s
                   AND e.pub_date < %s
                   AND e.published_at IS NULL
                   AND t.stage IN ({_USER_CHAIN_SQL})
                   AND NOT EXISTS (
                       SELECT 1
                         FROM user_episode_inbox i
                        WHERE i.user_id = %s
                          AND i.episode_id = e.id
                   )
                   AND (
                       t.status IN ('pending', 'processing')
                       OR (
                           t.status = 'retry_scheduled'
                           AND t.retry_count < t.max_retries
                           AND t.next_retry_at IS NOT NULL
                       )
                   )
                """,
                (user_id, since, cutoff, user_id),
            ).fetchone()
            return int(row["n"]) if row else 0

    def list_for_user(self, user_id: str, *, limit: int, offset: int) -> List[Briefing]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_COLS}
                  FROM user_briefings
                 WHERE user_id = %s
                 ORDER BY created_at DESC
                 LIMIT %s OFFSET %s
                """,
                (user_id, limit, offset),
            ).fetchall()
            return [self._row_to_briefing(row) for row in rows]

    def count_for_user(self, user_id: str) -> int:
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM user_briefings WHERE user_id = %s",
                (user_id,),
            ).fetchone()
            return int(row["n"])

    def update_listened_at(self, briefing_id: str, listened_at: datetime) -> Optional[Briefing]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                UPDATE user_briefings
                   SET listened_at = %s
                 WHERE id = %s
                RETURNING {_COLS}
                """,
                (listened_at, briefing_id),
            ).fetchone()
            return self._row_to_briefing(row) if row else None

    @staticmethod
    def _row_to_briefing(row: dict) -> Briefing:
        return Briefing(
            id=as_str(row["id"]),
            user_id=as_str(row["user_id"]),
            cursor_from=row["cursor_from"],
            cursor_to=row["cursor_to"],
            episode_count=row["episode_count"],
            script_path=row["script_path"],
            audio_path=row["audio_path"],
            created_at=row["created_at"],
            listened_at=row["listened_at"],
        )
