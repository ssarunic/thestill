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
SQLite implementation of briefing repository.

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

from ..models.briefing import Briefing, BriefingStatus
from .briefing_repository import BriefingRepository

logger = get_logger(__name__)


class SqliteBriefingRepository(BriefingRepository):
    """
    SQLite-based briefing repository.

    Thread-safety: Uses context manager for per-operation connections.
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite briefing repository.

        Args:
            db_path: Path to SQLite database file (e.g., "./data/podcasts.db")
        """
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite briefing repository", db_path=str(self.db_path))

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

    def get_by_id(self, briefing_id: str) -> Optional[Briefing]:
        """Get briefing by internal UUID (primary key)."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, created_at, updated_at, period_start, period_end,
                       status, file_path, episodes_total, episodes_completed,
                       episodes_failed, processing_time_seconds, error_message
                FROM briefings
                WHERE id = ?
                """,
                (briefing_id,),
            )

            row = cursor.fetchone()
            if not row:
                return None

            # Load episode IDs
            episode_ids = self._load_episode_ids(conn, briefing_id)
            return self._row_to_briefing(row, episode_ids)

    def get_all(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[BriefingStatus] = None,
        user_id: Optional[str] = None,
    ) -> List[Briefing]:
        """Get all briefings with optional filtering."""
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
                FROM briefings
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            )

            rows = cursor.fetchall()
            if not rows:
                return []

            # Batch load episode IDs for all briefings (avoids N+1 queries)
            briefing_ids = [row["id"] for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, briefing_ids)

            briefings = []
            for row in rows:
                episode_ids = episode_ids_map.get(row["id"], [])
                briefings.append(self._row_to_briefing(row, episode_ids))

            return briefings

    def get_latest(self) -> Optional[Briefing]:
        """Get the most recently created briefing."""
        briefings = self.get_all(limit=1)
        return briefings[0] if briefings else None

    def save(self, briefing: Briefing) -> Briefing:
        """
        Save or update a briefing.

        Uses UPSERT (INSERT ... ON CONFLICT) for atomic create-or-update.
        """
        with self._get_connection() as conn:
            # Update the updated_at timestamp
            briefing.updated_at = datetime.now(timezone.utc)

            conn.execute(
                """
                INSERT INTO briefings (
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
                    briefing.id,
                    briefing.user_id,
                    briefing.created_at.isoformat(),
                    briefing.updated_at.isoformat(),
                    briefing.period_start.isoformat(),
                    briefing.period_end.isoformat(),
                    briefing.status.value,
                    briefing.file_path,
                    briefing.episodes_total,
                    briefing.episodes_completed,
                    briefing.episodes_failed,
                    briefing.processing_time_seconds,
                    briefing.error_message,
                ),
            )

            # Update episode associations
            # First, remove existing associations
            conn.execute(
                "DELETE FROM briefing_episodes WHERE briefing_id = ?",
                (briefing.id,),
            )

            # Then insert new associations
            if briefing.episode_ids:
                conn.executemany(
                    "INSERT INTO briefing_episodes (briefing_id, episode_id) VALUES (?, ?)",
                    [(briefing.id, ep_id) for ep_id in briefing.episode_ids],
                )

            logger.debug(
                "Saved briefing",
                briefing_id=briefing.id,
                status=briefing.status.value,
                episode_count=len(briefing.episode_ids),
            )
            return briefing

    def delete(self, briefing_id: str) -> bool:
        """Delete briefing by ID."""
        with self._get_connection() as conn:
            # Episode associations are deleted via ON DELETE CASCADE
            cursor = conn.execute(
                "DELETE FROM briefings WHERE id = ?",
                (briefing_id,),
            )

            deleted = cursor.rowcount > 0
            if deleted:
                logger.info("Deleted briefing", briefing_id=briefing_id)
            return deleted

    def get_episodes_in_briefing(self, briefing_id: str) -> List[str]:
        """Get list of episode IDs included in a briefing."""
        with self._get_connection() as conn:
            return self._load_episode_ids(conn, briefing_id)

    def is_episode_in_any_briefing(self, episode_id: str) -> bool:
        """Check if an episode has been included in any briefing."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM briefing_episodes
                WHERE episode_id = ?
                LIMIT 1
                """,
                (episode_id,),
            )
            return cursor.fetchone() is not None

    def get_briefings_containing_episode(self, episode_id: str, user_id: Optional[str] = None) -> List[Briefing]:
        """Get all briefings that contain a specific episode."""
        with self._get_connection() as conn:
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT d.id, d.user_id, d.created_at, d.updated_at, d.period_start, d.period_end,
                           d.status, d.file_path, d.episodes_total, d.episodes_completed,
                           d.episodes_failed, d.processing_time_seconds, d.error_message
                    FROM briefings d
                    INNER JOIN briefing_episodes de ON d.id = de.briefing_id
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
                    FROM briefings d
                    INNER JOIN briefing_episodes de ON d.id = de.briefing_id
                    WHERE de.episode_id = ?
                    ORDER BY d.created_at DESC
                    """,
                    (episode_id,),
                )

            briefings = []
            for row in cursor.fetchall():
                episode_ids = self._load_episode_ids(conn, row["id"])
                briefings.append(self._row_to_briefing(row, episode_ids))

            return briefings

    def get_briefings_in_period(
        self,
        start: datetime,
        end: datetime,
        user_id: Optional[str] = None,
    ) -> List[Briefing]:
        """Get briefings whose period overlaps with the given time range."""
        with self._get_connection() as conn:
            # Overlap condition: briefing.period_start <= end AND briefing.period_end >= start
            if user_id is not None:
                cursor = conn.execute(
                    """
                    SELECT id, user_id, created_at, updated_at, period_start, period_end,
                           status, file_path, episodes_total, episodes_completed,
                           episodes_failed, processing_time_seconds, error_message
                    FROM briefings
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
                    FROM briefings
                    WHERE period_start <= ? AND period_end >= ?
                    ORDER BY created_at DESC
                    """,
                    (end.isoformat(), start.isoformat()),
                )

            rows = cursor.fetchall()
            if not rows:
                return []

            # Batch load episode IDs for all briefings (avoids N+1 queries)
            briefing_ids = [row["id"] for row in rows]
            episode_ids_map = self._load_episode_ids_batch(conn, briefing_ids)

            briefings = []
            for row in rows:
                episode_ids = episode_ids_map.get(row["id"], [])
                briefings.append(self._row_to_briefing(row, episode_ids))

            return briefings

    def count(
        self,
        status: Optional[BriefingStatus] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Count briefings with optional filtering."""
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
                f"SELECT COUNT(*) as cnt FROM briefings {where_clause}",
                tuple(params),
            )

            row = cursor.fetchone()
            return row["cnt"] if row else 0

    def _load_episode_ids(self, conn: sqlite3.Connection, briefing_id: str) -> List[str]:
        """Load episode IDs for a briefing."""
        cursor = conn.execute(
            "SELECT episode_id FROM briefing_episodes WHERE briefing_id = ?",
            (briefing_id,),
        )
        return [row["episode_id"] for row in cursor.fetchall()]

    def _load_episode_ids_batch(self, conn: sqlite3.Connection, briefing_ids: List[str]) -> Dict[str, List[str]]:
        """Load episode IDs for multiple briefings in a single query."""
        if not briefing_ids:
            return {}

        # Build parameterized query with placeholders
        placeholders = ",".join("?" * len(briefing_ids))
        cursor = conn.execute(
            f"SELECT briefing_id, episode_id FROM briefing_episodes WHERE briefing_id IN ({placeholders})",
            tuple(briefing_ids),
        )

        # Group episode IDs by briefing
        result: Dict[str, List[str]] = {briefing_id: [] for briefing_id in briefing_ids}
        for row in cursor.fetchall():
            result[row["briefing_id"]].append(row["episode_id"])

        return result

    def _row_to_briefing(self, row: sqlite3.Row, episode_ids: List[str]) -> Briefing:
        """Convert database row to Briefing model."""
        return Briefing(
            id=row["id"],
            user_id=row["user_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            status=BriefingStatus(row["status"]),
            file_path=row["file_path"],
            episode_ids=episode_ids,
            episodes_total=row["episodes_total"],
            episodes_completed=row["episodes_completed"],
            episodes_failed=row["episodes_failed"],
            processing_time_seconds=row["processing_time_seconds"],
            error_message=row["error_message"],
        )
