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
SQLite implementation of podcast follower repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Follows the same patterns as SqliteUserRepository
- Thread-safe via connection-per-operation
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List

from structlog import get_logger

from ..models.user import PodcastFollower
from .podcast_follower_repository import PodcastFollowerRepository

logger = get_logger(__name__)


class SqlitePodcastFollowerRepository(PodcastFollowerRepository):
    """
    SQLite-based podcast follower repository.

    Thread-safety: Uses context manager for per-operation connections.
    """

    def __init__(self, db_path: str):
        """
        Initialize SQLite podcast follower repository.

        Args:
            db_path: Path to SQLite database file (e.g., "./data/podcasts.db")
        """
        self.db_path = Path(db_path)
        logger.info(f"Initialized SQLite podcast follower repository: {self.db_path}")

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

    def add(self, follower: PodcastFollower) -> PodcastFollower:
        """Add a follower relationship."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO podcast_followers (id, user_id, podcast_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    follower.id,
                    follower.user_id,
                    follower.podcast_id,
                    follower.created_at.isoformat(),
                ),
            )

            logger.debug(f"Added follower: user={follower.user_id}, podcast={follower.podcast_id}")
            return follower

    def remove(self, user_id: str, podcast_id: str) -> bool:
        """Remove a follower relationship."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM podcast_followers
                WHERE user_id = ? AND podcast_id = ?
                """,
                (user_id, podcast_id),
            )

            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug(f"Removed follower: user={user_id}, podcast={podcast_id}")
            return deleted

    def exists(self, user_id: str, podcast_id: str) -> bool:
        """Check if a follower relationship exists."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT 1 FROM podcast_followers
                WHERE user_id = ? AND podcast_id = ?
                """,
                (user_id, podcast_id),
            )

            return cursor.fetchone() is not None

    def get_by_user(self, user_id: str) -> List[PodcastFollower]:
        """Get all podcasts followed by a user."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, podcast_id, created_at
                FROM podcast_followers
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            )

            return [self._row_to_follower(row) for row in cursor.fetchall()]

    def get_by_podcast(self, podcast_id: str) -> List[PodcastFollower]:
        """Get all followers of a podcast."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, user_id, podcast_id, created_at
                FROM podcast_followers
                WHERE podcast_id = ?
                ORDER BY created_at DESC
                """,
                (podcast_id,),
            )

            return [self._row_to_follower(row) for row in cursor.fetchall()]

    def count_by_podcast(self, podcast_id: str) -> int:
        """Count followers for a podcast."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) as count
                FROM podcast_followers
                WHERE podcast_id = ?
                """,
                (podcast_id,),
            )

            row = cursor.fetchone()
            return row["count"] if row else 0

    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """Get IDs of podcasts a user follows."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT podcast_id
                FROM podcast_followers
                WHERE user_id = ?
                """,
                (user_id,),
            )

            return [row["podcast_id"] for row in cursor.fetchall()]

    def _row_to_follower(self, row: sqlite3.Row) -> PodcastFollower:
        """Convert database row to PodcastFollower model."""
        return PodcastFollower(
            id=row["id"],
            user_id=row["user_id"],
            podcast_id=row["podcast_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
