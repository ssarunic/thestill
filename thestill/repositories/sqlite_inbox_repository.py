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

"""
SQLite implementation of ``InboxRepository``.

Reads compose ``InboxEntry`` + ``Episode`` + ``PodcastInboxSummary`` in a
single JOIN, so the API layer can serialize a list response without a
round-trip per row.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from structlog import get_logger

from ..models.inbox import InboxEntry, InboxItem, PodcastInboxSummary
from .inbox_repository import InboxRepository
from .sqlite_podcast_repository import episode_from_row

logger = get_logger(__name__)


# Episode columns aliased to ``ep_*`` in the inbox-list JOIN. The list is
# materialized at module load from the Episode model so it cannot drift.
_EPISODE_COLUMNS = (
    "id",
    "podcast_id",
    "created_at",
    "updated_at",
    "external_id",
    "title",
    "slug",
    "description",
    "description_html",
    "pub_date",
    "audio_url",
    "duration",
    "image_url",
    "explicit",
    "episode_type",
    "episode_number",
    "season_number",
    "website_url",
    "audio_file_size",
    "audio_mime_type",
    "audio_path",
    "downsampled_audio_path",
    "raw_transcript_path",
    "clean_transcript_path",
    "clean_transcript_json_path",
    "summary_path",
    "playback_time_offset_seconds",
    "published_at",
    "failed_at_stage",
    "failure_reason",
    "failure_type",
    "failed_at",
)


class SqliteInboxRepository(InboxRepository):
    """SQLite-based per-user inbox repository."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite inbox repository", db_path=str(self.db_path))

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

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def insert_many(self, entries: List[InboxEntry]) -> int:
        if not entries:
            return 0

        rows = [
            (
                entry.id,
                entry.user_id,
                entry.episode_id,
                entry.source,
                entry.state,
                entry.delivered_at.isoformat(),
                entry.state_changed_at.isoformat() if entry.state_changed_at else None,
            )
            for entry in entries
        ]

        # ``ON CONFLICT(user_id, episode_id) DO NOTHING`` preserves the
        # idempotency we want on the unique pair while still surfacing CHECK
        # / NOT NULL / FK violations — unlike ``INSERT OR IGNORE`` which
        # silently swallows every constraint failure.
        with self._get_connection() as conn:
            cursor = conn.executemany(
                """
                INSERT INTO user_episode_inbox
                    (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, episode_id) DO NOTHING
                """,
                rows,
            )
            return cursor.rowcount if cursor.rowcount is not None else 0

    def update_state(
        self, user_id: str, episode_id: str, state: str, state_changed_at: datetime
    ) -> Optional[InboxEntry]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                UPDATE user_episode_inbox
                   SET state = ?, state_changed_at = ?
                 WHERE user_id = ? AND episode_id = ?
                RETURNING id, user_id, episode_id, source, state, delivered_at, state_changed_at
                """,
                (state, state_changed_at.isoformat(), user_id, episode_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_entry(row)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, user_id: str, episode_id: str) -> Optional[InboxEntry]:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, episode_id, source, state, delivered_at, state_changed_at
                  FROM user_episode_inbox
                 WHERE user_id = ? AND episode_id = ?
                """,
                (user_id, episode_id),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    def list_items(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        if limit <= 0:
            return []

        episode_select = ", ".join(f"e.{col} AS ep_{col}" for col in _EPISODE_COLUMNS)
        clauses = ["i.user_id = ?"]
        params: List[Any] = [user_id]
        if state is None:
            # Default view excludes dismissed; pass ``state='dismissed'``
            # explicitly to surface those rows.
            clauses.append("i.state != 'dismissed'")
        else:
            clauses.append("i.state = ?")
            params.append(state)
        if before is not None:
            clauses.append("i.delivered_at < ?")
            params.append(before.isoformat())
        where = " AND ".join(clauses)
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    i.id AS i_id,
                    i.user_id AS i_user_id,
                    i.episode_id AS i_episode_id,
                    i.source AS i_source,
                    i.state AS i_state,
                    i.delivered_at AS i_delivered_at,
                    i.state_changed_at AS i_state_changed_at,
                    {episode_select},
                    p.id AS p_id,
                    p.title AS p_title,
                    p.slug AS p_slug,
                    p.image_url AS p_image_url
                  FROM user_episode_inbox i
                  JOIN episodes e ON e.id = i.episode_id
                  JOIN podcasts p ON p.id = e.podcast_id
                 WHERE {where}
                 ORDER BY i.delivered_at DESC
                 LIMIT ?
                """,
                params,
            ).fetchall()

        return [self._row_to_item(row) for row in rows]

    def unread_count(self, user_id: str) -> int:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                  FROM user_episode_inbox
                 WHERE user_id = ? AND state = 'unread'
                """,
                (user_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def recent_published_episode_ids(self, podcast_id: str, limit: int) -> List[str]:
        if limit <= 0:
            return []
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id
                  FROM episodes
                 WHERE podcast_id = ?
                   AND published_at IS NOT NULL
                 ORDER BY published_at DESC
                 LIMIT ?
                """,
                (podcast_id, limit),
            ).fetchall()
            return [row["id"] for row in rows]

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> InboxEntry:
        return InboxEntry(
            id=row["id"],
            user_id=row["user_id"],
            episode_id=row["episode_id"],
            source=row["source"],
            state=row["state"],
            delivered_at=datetime.fromisoformat(row["delivered_at"]),
            state_changed_at=(datetime.fromisoformat(row["state_changed_at"]) if row["state_changed_at"] else None),
        )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> InboxItem:
        entry = InboxEntry(
            id=row["i_id"],
            user_id=row["i_user_id"],
            episode_id=row["i_episode_id"],
            source=row["i_source"],
            state=row["i_state"],
            delivered_at=datetime.fromisoformat(row["i_delivered_at"]),
            state_changed_at=(datetime.fromisoformat(row["i_state_changed_at"]) if row["i_state_changed_at"] else None),
        )
        episode = episode_from_row(row, prefix="ep_")
        podcast = PodcastInboxSummary(
            id=row["p_id"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            image_url=row["p_image_url"],
        )
        return InboxItem(entry=entry, episode=episode, podcast=podcast)


__all__: Iterable[str] = ("SqliteInboxRepository",)
