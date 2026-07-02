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

"""PostgreSQL implementation of ``DigestRepository`` (spec #44).

Port of ``SqliteDigestRepository`` following the ``utils.postgres_ext``
conventions: native ``uuid`` ids (``digests.id``/``user_id`` and both
``digest_episodes`` columns), ``timestamptz`` datetimes (no isoformat
round-trips), ``%s`` placeholders. Schema in ``postgres_schema.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import psycopg
from structlog import get_logger

from ..models.digest import Digest, DigestStatus
from ..utils.postgres_ext import as_str, connect
from .digest_repository import DigestRepository

logger = get_logger(__name__)

_COLS = (
    "id, user_id, created_at, updated_at, period_start, period_end, "
    "status, file_path, episodes_total, episodes_completed, "
    "episodes_failed, processing_time_seconds, error_message"
)

# Same columns, d.-prefixed for the digest_episodes join queries.
_D_COLS = ", ".join(f"d.{col}" for col in _COLS.split(", "))


class PostgresDigestRepository(DigestRepository):
    """PostgreSQL-backed digest repository. Thread-safe via connection-per-op."""

    def __init__(self, dsn: str):
        """
        Args:
            dsn: psycopg connection string, e.g.
                ``postgresql://user:pass@host:5432/thestill``.
        """
        self.dsn = dsn
        logger.info("Initialized Postgres digest repository")

    def get_by_id(self, digest_id: str) -> Optional[Digest]:
        """Get digest by internal UUID (primary key)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM digests WHERE id = %s",
                (digest_id,),
            ).fetchone()
            if not row:
                return None

            episode_ids = self._load_episode_ids(conn, digest_id)
            return self._row_to_digest(row, episode_ids)

    def get_all(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[DigestStatus] = None,
        user_id: Optional[str] = None,
    ) -> List[Digest]:
        """Get all digests with optional filtering."""
        with connect(self.dsn) as conn:
            # Build WHERE clause dynamically
            conditions = []
            params: list = []

            if status:
                conditions.append("status = %s")
                params.append(status.value)

            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            rows = conn.execute(
                f"""
                SELECT {_COLS}
                FROM digests
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            ).fetchall()
            if not rows:
                return []

            # Batch load episode IDs for all digests (avoids N+1 queries)
            digest_ids = [as_str(row["id"]) for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, digest_ids)

            digests = []
            for row in rows:
                episode_ids = episode_ids_map.get(as_str(row["id"]), [])
                digests.append(self._row_to_digest(row, episode_ids))

            return digests

    def get_latest(self) -> Optional[Digest]:
        """Get the most recently created digest."""
        digests = self.get_all(limit=1)
        return digests[0] if digests else None

    def save(self, digest: Digest) -> Digest:
        """
        Save or update a digest.

        Uses UPSERT (INSERT ... ON CONFLICT) for atomic create-or-update.
        """
        with connect(self.dsn) as conn:
            # Update the updated_at timestamp
            digest.updated_at = datetime.now(timezone.utc)

            conn.execute(
                """
                INSERT INTO digests (
                    id, user_id, created_at, updated_at, period_start, period_end,
                    status, file_path, episodes_total, episodes_completed,
                    episodes_failed, processing_time_seconds, error_message
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    status = EXCLUDED.status,
                    file_path = EXCLUDED.file_path,
                    episodes_total = EXCLUDED.episodes_total,
                    episodes_completed = EXCLUDED.episodes_completed,
                    episodes_failed = EXCLUDED.episodes_failed,
                    processing_time_seconds = EXCLUDED.processing_time_seconds,
                    error_message = EXCLUDED.error_message
                """,
                (
                    digest.id,
                    digest.user_id,
                    digest.created_at,
                    digest.updated_at,
                    digest.period_start,
                    digest.period_end,
                    digest.status.value,
                    digest.file_path,
                    digest.episodes_total,
                    digest.episodes_completed,
                    digest.episodes_failed,
                    digest.processing_time_seconds,
                    digest.error_message,
                ),
            )

            # Update episode associations
            # First, remove existing associations
            conn.execute(
                "DELETE FROM digest_episodes WHERE digest_id = %s",
                (digest.id,),
            )

            # Then insert new associations
            if digest.episode_ids:
                with conn.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO digest_episodes (digest_id, episode_id) VALUES (%s, %s)",
                        [(digest.id, ep_id) for ep_id in digest.episode_ids],
                    )

            logger.debug(
                "Saved digest",
                digest_id=digest.id,
                status=digest.status.value,
                episode_count=len(digest.episode_ids),
            )
            return digest

    def delete(self, digest_id: str) -> bool:
        """Delete digest by ID."""
        with connect(self.dsn) as conn:
            # Episode associations are deleted via ON DELETE CASCADE
            cursor = conn.execute(
                "DELETE FROM digests WHERE id = %s",
                (digest_id,),
            )

            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Deleted digest", digest_id=digest_id)
            return deleted

    def get_episodes_in_digest(self, digest_id: str) -> List[str]:
        """Get list of episode IDs included in a digest."""
        with connect(self.dsn) as conn:
            return self._load_episode_ids(conn, digest_id)

    def is_episode_in_any_digest(self, episode_id: str) -> bool:
        """Check if an episode has been included in any digest."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM digest_episodes
                WHERE episode_id = %s
                LIMIT 1
                """,
                (episode_id,),
            ).fetchone()
            return row is not None

    def get_digests_containing_episode(self, episode_id: str, user_id: Optional[str] = None) -> List[Digest]:
        """Get all digests that contain a specific episode."""
        with connect(self.dsn) as conn:
            if user_id is not None:
                rows = conn.execute(
                    f"""
                    SELECT {_D_COLS}
                    FROM digests d
                    INNER JOIN digest_episodes de ON d.id = de.digest_id
                    WHERE de.episode_id = %s AND d.user_id = %s
                    ORDER BY d.created_at DESC
                    """,
                    (episode_id, user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT {_D_COLS}
                    FROM digests d
                    INNER JOIN digest_episodes de ON d.id = de.digest_id
                    WHERE de.episode_id = %s
                    ORDER BY d.created_at DESC
                    """,
                    (episode_id,),
                ).fetchall()

            digests = []
            for row in rows:
                episode_ids = self._load_episode_ids(conn, as_str(row["id"]))
                digests.append(self._row_to_digest(row, episode_ids))

            return digests

    def get_digests_in_period(
        self,
        start: datetime,
        end: datetime,
        user_id: Optional[str] = None,
    ) -> List[Digest]:
        """Get digests whose period overlaps with the given time range."""
        with connect(self.dsn) as conn:
            # Overlap condition: digest.period_start <= end AND digest.period_end >= start
            if user_id is not None:
                rows = conn.execute(
                    f"""
                    SELECT {_COLS}
                    FROM digests
                    WHERE period_start <= %s AND period_end >= %s AND user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (end, start, user_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT {_COLS}
                    FROM digests
                    WHERE period_start <= %s AND period_end >= %s
                    ORDER BY created_at DESC
                    """,
                    (end, start),
                ).fetchall()

            if not rows:
                return []

            # Batch load episode IDs for all digests (avoids N+1 queries)
            digest_ids = [as_str(row["id"]) for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, digest_ids)

            digests = []
            for row in rows:
                episode_ids = episode_ids_map.get(as_str(row["id"]), [])
                digests.append(self._row_to_digest(row, episode_ids))

            return digests

    def count(
        self,
        status: Optional[DigestStatus] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Count digests with optional filtering."""
        with connect(self.dsn) as conn:
            # Build WHERE clause dynamically
            conditions = []
            params: list = []

            if status:
                conditions.append("status = %s")
                params.append(status.value)

            if user_id is not None:
                conditions.append("user_id = %s")
                params.append(user_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM digests {where_clause}",
                tuple(params),
            ).fetchone()
            return row["cnt"] if row else 0

    def _load_episode_ids(self, conn: psycopg.Connection, digest_id: str) -> List[str]:
        """Load episode IDs for a digest (uuid reads stringified)."""
        rows = conn.execute(
            "SELECT episode_id FROM digest_episodes WHERE digest_id = %s",
            (digest_id,),
        ).fetchall()
        return [as_str(row["episode_id"]) for row in rows]

    def _load_episode_ids_batch(self, conn: psycopg.Connection, digest_ids: List[str]) -> Dict[str, List[str]]:
        """Load episode IDs for multiple digests in a single query."""
        if not digest_ids:
            return {}

        # str list binds as text[]; cast to uuid[] for the ANY comparison.
        rows = conn.execute(
            "SELECT digest_id, episode_id FROM digest_episodes WHERE digest_id = ANY(%s::uuid[])",
            (digest_ids,),
        ).fetchall()

        # Group episode IDs by digest
        result: Dict[str, List[str]] = {digest_id: [] for digest_id in digest_ids}
        for row in rows:
            result[as_str(row["digest_id"])].append(as_str(row["episode_id"]))

        return result

    def _row_to_digest(self, row: dict, episode_ids: List[str]) -> Digest:
        """Convert a dict row to a Digest. timestamptz columns come back as
        tz-aware ``datetime`` — no string parsing needed (unlike SQLite)."""
        return Digest(
            id=as_str(row["id"]),
            user_id=as_str(row["user_id"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            status=DigestStatus(row["status"]),
            file_path=row["file_path"],
            episode_ids=episode_ids,
            episodes_total=row["episodes_total"],
            episodes_completed=row["episodes_completed"],
            episodes_failed=row["episodes_failed"],
            processing_time_seconds=row["processing_time_seconds"],
            error_message=row["error_message"],
        )
