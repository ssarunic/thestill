# Copyright 2025 thestill.me
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
SQLite implementation of digest repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Follows the same patterns as SqliteUserRepository
- Thread-safe via connection-per-operation
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from structlog import get_logger

from ..models.digest import Digest, DigestStatus
from .digest_repository import DigestRepository

logger = get_logger(__name__)


class SqliteDigestRepository(DigestRepository):
    """
    SQLite-based digest repository.

    Thread-safety: Uses context manager for per-operation connections.
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite digest repository.

        Args:
            db_path: Path to SQLite database file (e.g., "./data/podcasts.db")
        """
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite digest repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> sqlite3.Connection:
        """
        Get database connection with proper setup.

        Features:
        - Row factory for dict-like access
        - Foreign keys enabled
        - Automatic commit/rollback
        """
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

    def get_by_id(self, digest_id: str) -> Optional[Digest]:
        """Get digest by internal UUID (primary key)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, created_at, updated_at, period_start, period_end,
                       status, file_path, episodes_total, episodes_completed,
                       episodes_failed, processing_time_seconds, error_message
                FROM digests
                WHERE id = ?
                """,
                (digest_id,),
            )

            row = cursor.fetchone()
            if not row:
                return None

            # Load episode IDs
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
        with self._get_connection() as conn:
            # Build WHERE clause dynamically
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status.value)

            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor = conn.execute(
                f"""
                SELECT id, user_id, created_at, updated_at, period_start, period_end,
                       status, file_path, episodes_total, episodes_completed,
                       episodes_failed, processing_time_seconds, error_message
                FROM digests
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )

            rows = cursor.fetchall()
            if not rows:
                return []

            # Batch load episode IDs for all digests (avoids N+1 queries)
            digest_ids = [row["id"] for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, digest_ids)

            digests = []
            for row in rows:
                episode_ids = episode_ids_map.get(row["id"], [])
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
        with self._get_connection() as conn:
            # Update the updated_at timestamp
            digest.updated_at = datetime.now(timezone.utc)

            conn.execute(
                """
                INSERT INTO digests (
                    id, user_id, created_at, updated_at, period_start, period_end,
                    status, file_path, episodes_total, episodes_completed,
                    episodes_failed, processing_time_seconds, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    status = excluded.status,
                    file_path = excluded.file_path,
                    episodes_total = excluded.episodes_total,
                    episodes_completed = excluded.episodes_completed,
                    episodes_failed = excluded.episodes_failed,
                    processing_time_seconds = excluded.processing_time_seconds,
                    error_message = excluded.error_message
                """,
                (
                    digest.id,
                    digest.user_id,
                    digest.created_at.isoformat(),
                    digest.updated_at.isoformat(),
                    digest.period_start.isoformat(),
                    digest.period_end.isoformat(),
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
                "DELETE FROM digest_episodes WHERE digest_id = ?",
                (digest.id,),
            )

            # Then insert new associations
            if digest.episode_ids:
                conn.executemany(
                    "INSERT INTO digest_episodes (digest_id, episode_id) VALUES (?, ?)",
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
        with self._get_connection() as conn:
            # Episode associations are deleted via ON DELETE CASCADE
            cursor = conn.execute(
                "DELETE FROM digests WHERE id = ?",
                (digest_id,),
            )

            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Deleted digest", digest_id=digest_id)
            return deleted

    def get_episodes_in_digest(self, digest_id: str) -> List[str]:
        """Get list of episode IDs included in a digest."""
        with self._get_connection() as conn:
            return self._load_episode_ids(conn, digest_id)

    def is_episode_in_any_digest(self, episode_id: str) -> bool:
        """Check if an episode has been included in any digest."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM digest_episodes
                WHERE episode_id = ?
                LIMIT 1
                """,
                (episode_id,),
            )
            return cursor.fetchone() is not None

    def get_digests_containing_episode(self, episode_id: str, user_id: Optional[str] = None) -> List[Digest]:
        """Get all digests that contain a specific episode."""
        with self._get_connection() as conn:
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT d.id, d.user_id, d.created_at, d.updated_at, d.period_start, d.period_end,
                           d.status, d.file_path, d.episodes_total, d.episodes_completed,
                           d.episodes_failed, d.processing_time_seconds, d.error_message
                    FROM digests d
                    INNER JOIN digest_episodes de ON d.id = de.digest_id
                    WHERE de.episode_id = ? AND d.user_id = ?
                    ORDER BY d.created_at DESC
                    """,
                    (episode_id, user_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT d.id, d.user_id, d.created_at, d.updated_at, d.period_start, d.period_end,
                           d.status, d.file_path, d.episodes_total, d.episodes_completed,
                           d.episodes_failed, d.processing_time_seconds, d.error_message
                    FROM digests d
                    INNER JOIN digest_episodes de ON d.id = de.digest_id
                    WHERE de.episode_id = ?
                    ORDER BY d.created_at DESC
                    """,
                    (episode_id,),
                )

            digests = []
            for row in cursor.fetchall():
                episode_ids = self._load_episode_ids(conn, row["id"])
                digests.append(self._row_to_digest(row, episode_ids))

            return digests

    def get_digests_in_period(
        self,
        start: datetime,
        end: datetime,
        user_id: Optional[str] = None,
    ) -> List[Digest]:
        """Get digests whose period overlaps with the given time range."""
        with self._get_connection() as conn:
            # Overlap condition: digest.period_start <= end AND digest.period_end >= start
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT id, user_id, created_at, updated_at, period_start, period_end,
                           status, file_path, episodes_total, episodes_completed,
                           episodes_failed, processing_time_seconds, error_message
                    FROM digests
                    WHERE period_start <= ? AND period_end >= ? AND user_id = ?
                    ORDER BY created_at DESC
                    """,
                    (end.isoformat(), start.isoformat(), user_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT id, user_id, created_at, updated_at, period_start, period_end,
                           status, file_path, episodes_total, episodes_completed,
                           episodes_failed, processing_time_seconds, error_message
                    FROM digests
                    WHERE period_start <= ? AND period_end >= ?
                    ORDER BY created_at DESC
                    """,
                    (end.isoformat(), start.isoformat()),
                )

            rows = cursor.fetchall()
            if not rows:
                return []

            # Batch load episode IDs for all digests (avoids N+1 queries)
            digest_ids = [row["id"] for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, digest_ids)

            digests = []
            for row in rows:
                episode_ids = episode_ids_map.get(row["id"], [])
                digests.append(self._row_to_digest(row, episode_ids))

            return digests

    def count(
        self,
        status: Optional[DigestStatus] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Count digests with optional filtering."""
        with self._get_connection() as conn:
            # Build WHERE clause dynamically
            conditions = []
            params = []

            if status:
                conditions.append("status = ?")
                params.append(status.value)

            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

            cursor = conn.execute(
                f"SELECT COUNT(*) as cnt FROM digests {where_clause}",
                tuple(params),
            )

            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def _load_episode_ids(self, conn: sqlite3.Connection, digest_id: str) -> List[str]:
        """Load episode IDs for a digest."""
        cursor = conn.execute(
            "SELECT episode_id FROM digest_episodes WHERE digest_id = ?",
            (digest_id,),
        )
        return [row["episode_id"] for row in cursor.fetchall()]

    def _load_episode_ids_batch(self, conn: sqlite3.Connection, digest_ids: List[str]) -> Dict[str, List[str]]:
        """Load episode IDs for multiple digests in a single query."""
        if not digest_ids:
            return {}

        # Build parameterized query with placeholders
        placeholders = ",".join("?" * len(digest_ids))
        cursor = conn.execute(
            f"SELECT digest_id, episode_id FROM digest_episodes WHERE digest_id IN ({placeholders})",
            tuple(digest_ids),
        )

        # Group episode IDs by digest
        result: Dict[str, List[str]] = {digest_id: [] for digest_id in digest_ids}
        for row in cursor.fetchall():
            result[row["digest_id"]].append(row["episode_id"])

        return result

    def _row_to_digest(self, row: sqlite3.Row, episode_ids: List[str]) -> Digest:
        """Convert database row to Digest model."""
        return Digest(
            id=row["id"],
            user_id=row["user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            status=DigestStatus(row["status"]),
            file_path=row["file_path"],
            episode_ids=episode_ids,
            episodes_total=row["episodes_total"],
            episodes_completed=row["episodes_completed"],
            episodes_failed=row["episodes_failed"],
            processing_time_seconds=row["processing_time_seconds"],
            error_message=row["error_message"],
        )
