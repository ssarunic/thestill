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

"""PostgreSQL implementation of ``InboxRepository`` (spec #44).

Port of ``SqliteInboxRepository`` following the ``utils.postgres_ext``
conventions: native ``uuid`` ids (str params in, ``as_str()`` out),
``timestamptz`` datetimes (no isoformat round-trips), ``%s`` placeholders.
Schema in ``postgres_schema.py``.

Reads compose ``InboxEntry`` + ``Episode`` + ``PodcastInboxSummary`` in a
single JOIN, so the API layer can serialize a list response without a
round-trip per row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Optional, Tuple

from structlog import get_logger

from ..models.inbox import INBOX_STATES_ELIGIBLE_FOR_BRIEFING, InboxEntry, InboxItem, InboxState, PodcastInboxSummary
from ..models.podcast import Episode, FailureType
from ..utils.postgres_ext import as_str, connect
from .inbox_repository import InboxRepository

logger = get_logger(__name__)

_ENTRY_COLS = "id, user_id, episode_id, source, state, delivered_at, state_changed_at"

# Episode columns aliased to ``ep_*`` in the inbox-list JOIN. Mirrors the
# SQLite port's list; the Postgres schema carries every column, so no
# legacy-migration guards are needed on read.
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


def _episode_from_row(row: dict, *, prefix: str = "") -> Episode:
    """Build an ``Episode`` from a dict row of native Postgres types.

    ``prefix`` lets composed-JOIN queries (the inbox list aliases episode
    columns to ``ep_*``) reuse the mapping. Unlike the SQLite mapper there is
    no string parsing: ``timestamptz`` columns arrive as tz-aware datetimes
    and ``boolean`` columns as Python bools; uuids are stringified.
    """

    def col(name: str):
        return row[f"{prefix}{name}"]

    failure_type = None
    if col("failure_type"):
        try:
            failure_type = FailureType(col("failure_type"))
        except ValueError:
            logger.warning("Unknown failure_type", failure_type=col("failure_type"), episode_id=as_str(col("id")))

    return Episode(
        id=as_str(col("id")),
        podcast_id=as_str(col("podcast_id")),
        created_at=col("created_at"),
        updated_at=col("updated_at"),
        external_id=col("external_id"),
        title=col("title"),
        slug=col("slug") or "",
        description=col("description"),
        description_html=col("description_html") or "",
        pub_date=col("pub_date"),
        audio_url=col("audio_url"),
        duration=col("duration"),
        image_url=col("image_url"),
        explicit=col("explicit"),
        episode_type=col("episode_type"),
        episode_number=col("episode_number"),
        season_number=col("season_number"),
        website_url=col("website_url"),
        audio_file_size=col("audio_file_size"),
        audio_mime_type=col("audio_mime_type"),
        audio_path=col("audio_path"),
        downsampled_audio_path=col("downsampled_audio_path"),
        raw_transcript_path=col("raw_transcript_path"),
        clean_transcript_path=col("clean_transcript_path"),
        clean_transcript_json_path=col("clean_transcript_json_path"),
        playback_time_offset_seconds=col("playback_time_offset_seconds"),
        summary_path=col("summary_path"),
        published_at=col("published_at"),
        failed_at_stage=col("failed_at_stage"),
        failure_reason=col("failure_reason"),
        failure_type=failure_type,
        failed_at=col("failed_at"),
    )


class PostgresInboxRepository(InboxRepository):
    """PostgreSQL-backed per-user inbox repository. Thread-safe via connection-per-op."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres inbox repository")

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
                entry.delivered_at,
                entry.state_changed_at,
            )
            for entry in entries
        ]

        # ``ON CONFLICT (user_id, episode_id) DO NOTHING`` preserves the
        # idempotency we want on the unique pair while still surfacing CHECK
        # / NOT NULL / FK violations. psycopg's ``executemany`` accumulates
        # ``rowcount`` across the batch, so conflicted rows don't count.
        with connect(self.dsn) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO user_episode_inbox
                        (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, episode_id) DO NOTHING
                    """,
                    rows,
                )
                return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount > 0 else 0

    def find_or_create(self, *, user_id: str, episode_id: str, source: str) -> Tuple[InboxEntry, bool]:
        # Single-statement insert-or-noop avoids the SELECT/INSERT race
        # between concurrent imports of the same URL by the same user.
        # RETURNING only fires when the row was actually inserted; an empty
        # result means a prior row already exists, which we then load.
        row_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            inserted = conn.execute(
                f"""
                INSERT INTO user_episode_inbox
                    (id, user_id, episode_id, source, state, delivered_at)
                VALUES (%s, %s, %s, %s, 'unread', %s)
                ON CONFLICT (user_id, episode_id) DO NOTHING
                RETURNING {_ENTRY_COLS}
                """,
                (row_id, user_id, episode_id, source, now),
            ).fetchone()
            if inserted is not None:
                return self._row_to_entry(inserted), True

            existing = conn.execute(
                f"""
                SELECT {_ENTRY_COLS}
                  FROM user_episode_inbox
                 WHERE user_id = %s AND episode_id = %s
                """,
                (user_id, episode_id),
            ).fetchone()
            return self._row_to_entry(existing), False

    def update_state(
        self, user_id: str, episode_id: str, state: str, state_changed_at: datetime
    ) -> Optional[InboxEntry]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                UPDATE user_episode_inbox
                   SET state = %s, state_changed_at = %s
                 WHERE user_id = %s AND episode_id = %s
                RETURNING {_ENTRY_COLS}
                """,
                (state, state_changed_at, user_id, episode_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_entry(row)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, user_id: str, episode_id: str) -> Optional[InboxEntry]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_ENTRY_COLS}
                  FROM user_episode_inbox
                 WHERE user_id = %s AND episode_id = %s
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
        clauses = ["i.user_id = %s"]
        params: List[Any] = [user_id]
        if state is None:
            # Default view excludes dismissed; pass ``state='dismissed'``
            # explicitly to surface those rows.
            clauses.append("i.state != 'dismissed'")
        else:
            clauses.append("i.state = %s")
            params.append(state)
        if before is not None:
            clauses.append("i.delivered_at < %s")
            params.append(before)
        where = " AND ".join(clauses)
        params.append(limit)

        with connect(self.dsn) as conn:
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
                 LIMIT %s
                """,
                params,
            ).fetchall()

        return [self._row_to_item(row) for row in rows]

    def count_imports_for_user_since(self, user_id: str, since: datetime) -> int:
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                  FROM user_episode_inbox
                 WHERE user_id = %s
                   AND source = 'import'
                   AND delivered_at >= %s
                """,
                (user_id, since),
            ).fetchone()
            return int(row["n"]) if row else 0

    def unread_count(self, user_id: str) -> int:
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                  FROM user_episode_inbox
                 WHERE user_id = %s AND state = 'unread'
                """,
                (user_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def recent_published_episode_ids(self, podcast_id: str, limit: int) -> List[str]:
        if limit <= 0:
            return []
        # Order by ``pub_date`` (RSS air date) so the seed reflects what
        # a listener thinks of as "the most recent episodes", not the
        # accident of which one the pipeline finished first. Falls back
        # to ``published_at`` for episodes whose feed entry lacks a
        # pub_date.
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id
                  FROM episodes
                 WHERE podcast_id = %s
                   AND published_at IS NOT NULL
                 ORDER BY COALESCE(pub_date, published_at) DESC
                 LIMIT %s
                """,
                (podcast_id, limit),
            ).fetchall()
            return [as_str(row["id"]) for row in rows]

    def list_episode_ids_in_window(
        self,
        user_id: str,
        *,
        since: datetime,
        until: datetime,
        states: Iterable[InboxState] = INBOX_STATES_ELIGIBLE_FOR_BRIEFING,
    ) -> List[str]:
        state_list = tuple(states)
        if not state_list:
            return []
        placeholders = ",".join("%s" for _ in state_list)
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT episode_id
                  FROM user_episode_inbox
                 WHERE user_id = %s
                   AND delivered_at >= %s
                   AND delivered_at < %s
                   AND state IN ({placeholders})
                 ORDER BY delivered_at ASC
                """,
                (user_id, since, until, *state_list),
            ).fetchall()
            return [as_str(row["episode_id"]) for row in rows]

    def backfill_existing_followers(self, limit: int, *, dry_run: bool = False) -> int:
        if limit <= 0:
            return 0

        # ROW_NUMBER picks the top-N most-recently-aired episodes per
        # podcast (by ``pub_date``, not pipeline-finish time). The LEFT
        # JOIN ... IS NULL filter excludes pairs already in the inbox
        # so dry-run and real-run agree on the count (without it,
        # dry-run would over-report rows that ON CONFLICT would skip).
        # ``rn`` then drives the per-(user, podcast) ``delivered_at``
        # stagger below so the newest seed lands at the top.
        select_sql = """
            SELECT f.user_id, ranked.id AS episode_id, ranked.rn
              FROM podcast_followers f
              JOIN (
                  SELECT id, podcast_id,
                         ROW_NUMBER() OVER (
                             PARTITION BY podcast_id
                             ORDER BY COALESCE(pub_date, published_at) DESC
                         ) AS rn
                    FROM episodes
                   WHERE published_at IS NOT NULL
              ) AS ranked
                ON ranked.podcast_id = f.podcast_id AND ranked.rn <= %s
              LEFT JOIN user_episode_inbox i
                ON i.user_id = f.user_id AND i.episode_id = ranked.id
             WHERE i.id IS NULL
        """

        with connect(self.dsn) as conn:
            candidates = conn.execute(select_sql, (limit,)).fetchall()
            if dry_run or not candidates:
                return len(candidates)

            base = datetime.now(timezone.utc)
            rows = [
                (
                    str(uuid.uuid4()),
                    as_str(row["user_id"]),
                    as_str(row["episode_id"]),
                    "follow_seed",
                    "unread",
                    base - timedelta(milliseconds=row["rn"] - 1),
                )
                for row in candidates
            ]
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO user_episode_inbox
                        (id, user_id, episode_id, source, state, delivered_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, episode_id) DO NOTHING
                    """,
                    rows,
                )
                return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount > 0 else 0

    # ------------------------------------------------------------------
    # Row mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: dict) -> InboxEntry:
        return InboxEntry(
            id=as_str(row["id"]),
            user_id=as_str(row["user_id"]),
            episode_id=as_str(row["episode_id"]),
            source=row["source"],
            state=row["state"],
            delivered_at=row["delivered_at"],
            state_changed_at=row["state_changed_at"],
        )

    @staticmethod
    def _row_to_item(row: dict) -> InboxItem:
        entry = InboxEntry(
            id=as_str(row["i_id"]),
            user_id=as_str(row["i_user_id"]),
            episode_id=as_str(row["i_episode_id"]),
            source=row["i_source"],
            state=row["i_state"],
            delivered_at=row["i_delivered_at"],
            state_changed_at=row["i_state_changed_at"],
        )
        episode = _episode_from_row(row, prefix="ep_")
        podcast = PodcastInboxSummary(
            id=as_str(row["p_id"]),
            title=row["p_title"],
            slug=row["p_slug"] or "",
            image_url=row["p_image_url"],
        )
        return InboxItem(entry=entry, episode=episode, podcast=podcast)


__all__: Iterable[str] = ("PostgresInboxRepository",)
