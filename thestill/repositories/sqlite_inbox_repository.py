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
SQLite implementation of ``InboxRepository`` (spec #29).

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

from ..models.inbox import (
    INBOX_SOURCES,
    INBOX_STATES,
    InboxEntry,
    InboxItem,
    PodcastInboxSummary,
)
from ..models.podcast import Episode, FailureType
from .inbox_repository import InboxRepository

logger = get_logger(__name__)


# Columns selected from ``episodes`` when composing an ``InboxItem``.
# Kept in sync with ``_row_to_episode`` in sqlite_podcast_repository.py.
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
    """
    SQLite-based per-user inbox repository.

    Thread-safety: per-operation connections via ``_get_connection``, matching
    the project pattern (``SqlitePodcastRepository``,
    ``SqlitePodcastFollowerRepository``).
    """

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
        for entry in entries:
            if entry.source not in INBOX_SOURCES:
                raise ValueError(f"Invalid inbox source: {entry.source}")
            if entry.state not in INBOX_STATES:
                raise ValueError(f"Invalid inbox state: {entry.state}")

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

        with self._get_connection() as conn:
            cursor = conn.executemany(
                """
                INSERT OR IGNORE INTO user_episode_inbox
                    (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            # ``rowcount`` after ``executemany`` reports total rows affected
            # (insertions only, since OR IGNORE silently skips conflicts).
            inserted = cursor.rowcount if cursor.rowcount is not None else 0
            return inserted

    def update_state(
        self, user_id: str, episode_id: str, state: str, state_changed_at: datetime
    ) -> Optional[InboxEntry]:
        if state not in INBOX_STATES:
            raise ValueError(f"Invalid inbox state: {state}")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE user_episode_inbox
                   SET state = ?, state_changed_at = ?
                 WHERE user_id = ? AND episode_id = ?
                """,
                (state, state_changed_at.isoformat(), user_id, episode_id),
            )
            if cursor.rowcount == 0:
                return None
            return self._fetch_entry(conn, user_id, episode_id)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, user_id: str, episode_id: str) -> Optional[InboxEntry]:
        with self._get_connection() as conn:
            return self._fetch_entry(conn, user_id, episode_id)

    def list_items(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        include_dismissed: bool = False,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        if state is not None and state not in INBOX_STATES:
            raise ValueError(f"Invalid inbox state filter: {state}")
        if limit <= 0:
            return []

        episode_select = ", ".join(f"e.{col} AS ep_{col}" for col in _EPISODE_COLUMNS)
        clauses = ["i.user_id = ?"]
        params: List[Any] = [user_id]
        if state is not None:
            clauses.append("i.state = ?")
            params.append(state)
        elif not include_dismissed:
            clauses.append("i.state != 'dismissed'")
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

    def followers_of_podcast(self, podcast_id: str) -> List[str]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT user_id FROM podcast_followers WHERE podcast_id = ?",
                (podcast_id,),
            ).fetchall()
            return [row["user_id"] for row in rows]

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

    def _fetch_entry(self, conn: sqlite3.Connection, user_id: str, episode_id: str) -> Optional[InboxEntry]:
        row = conn.execute(
            """
            SELECT id, user_id, episode_id, source, state, delivered_at, state_changed_at
              FROM user_episode_inbox
             WHERE user_id = ? AND episode_id = ?
            """,
            (user_id, episode_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

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

        explicit = None
        if row["ep_explicit"] is not None:
            explicit = row["ep_explicit"] == 1

        failure_type: Optional[FailureType] = None
        if row["ep_failure_type"]:
            try:
                failure_type = FailureType(row["ep_failure_type"])
            except ValueError:
                failure_type = None

        episode = Episode(
            id=row["ep_id"],
            podcast_id=row["ep_podcast_id"],
            created_at=datetime.fromisoformat(row["ep_created_at"]),
            updated_at=datetime.fromisoformat(row["ep_updated_at"]),
            external_id=row["ep_external_id"],
            title=row["ep_title"],
            slug=row["ep_slug"] or "",
            description=row["ep_description"],
            description_html=row["ep_description_html"] or "",
            pub_date=(datetime.fromisoformat(row["ep_pub_date"]) if row["ep_pub_date"] else None),
            audio_url=row["ep_audio_url"],
            duration=row["ep_duration"],
            image_url=row["ep_image_url"],
            explicit=explicit,
            episode_type=row["ep_episode_type"],
            episode_number=row["ep_episode_number"],
            season_number=row["ep_season_number"],
            website_url=row["ep_website_url"],
            audio_file_size=row["ep_audio_file_size"],
            audio_mime_type=row["ep_audio_mime_type"],
            audio_path=row["ep_audio_path"],
            downsampled_audio_path=row["ep_downsampled_audio_path"],
            raw_transcript_path=row["ep_raw_transcript_path"],
            clean_transcript_path=row["ep_clean_transcript_path"],
            clean_transcript_json_path=row["ep_clean_transcript_json_path"],
            summary_path=row["ep_summary_path"],
            playback_time_offset_seconds=row["ep_playback_time_offset_seconds"] or 0.0,
            published_at=(datetime.fromisoformat(row["ep_published_at"]) if row["ep_published_at"] else None),
            failed_at_stage=row["ep_failed_at_stage"],
            failure_reason=row["ep_failure_reason"],
            failure_type=failure_type,
            failed_at=(datetime.fromisoformat(row["ep_failed_at"]) if row["ep_failed_at"] else None),
        )

        podcast = PodcastInboxSummary(
            id=row["p_id"],
            title=row["p_title"],
            slug=row["p_slug"] or "",
            image_url=row["p_image_url"],
        )

        return InboxItem(entry=entry, episode=episode, podcast=podcast)


__all__: Iterable[str] = ("SqliteInboxRepository",)
