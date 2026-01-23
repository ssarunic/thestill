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
PostgreSQL implementation of podcast follower repository.

Design principles:
- Raw SQL with parameter binding (no ORM)
- Connection pooling via psycopg2 pool
- Thread-safe via connection pool
"""

import logging
from contextlib import contextmanager
from typing import List

import psycopg2
import psycopg2.extras
import psycopg2.pool

from ..models.user import PodcastFollower
from .podcast_follower_repository import PodcastFollowerRepository

logger = logging.getLogger(__name__)


class PostgresPodcastFollowerRepository(PodcastFollowerRepository):
    """
    PostgreSQL-based podcast follower repository.

    Thread-safety: Uses connection pool for thread-safe access.
    """

    def __init__(self, database_url: str, min_connections: int = 1, max_connections: int = 10):
        """
        Initialize PostgreSQL podcast follower repository.

        Args:
            database_url: PostgreSQL connection URL
            min_connections: Minimum connections in pool
            max_connections: Maximum connections in pool
        """
        self.database_url = database_url
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            min_connections,
            max_connections,
            database_url,
        )
        logger.info("Initialized PostgreSQL podcast follower repository")

    def close(self):
        """Close all connections in the pool."""
        if self._pool:
            self._pool.closeall()

    @contextmanager
    def _get_connection(self):
        """Get database connection from pool."""
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def add(self, follower: PodcastFollower) -> PodcastFollower:
        """Add a follower relationship."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO podcast_followers (id, user_id, podcast_id, created_at)
                    VALUES (%s, %s, %s, %s)
                """, (
                    follower.id,
                    follower.user_id,
                    follower.podcast_id,
                    follower.created_at,
                ))

            conn.commit()
            logger.debug(f"Added follower: user={follower.user_id}, podcast={follower.podcast_id}")
            return follower

    def remove(self, user_id: str, podcast_id: str) -> bool:
        """Remove a follower relationship."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    DELETE FROM podcast_followers
                    WHERE user_id = %s AND podcast_id = %s
                """, (user_id, podcast_id))

                deleted = cursor.rowcount > 0
            conn.commit()

            if deleted:
                logger.debug(f"Removed follower: user={user_id}, podcast={podcast_id}")
            return deleted

    def exists(self, user_id: str, podcast_id: str) -> bool:
        """Check if a follower relationship exists."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT 1 FROM podcast_followers
                    WHERE user_id = %s AND podcast_id = %s
                """, (user_id, podcast_id))

                return cursor.fetchone() is not None

    def get_by_user(self, user_id: str) -> List[PodcastFollower]:
        """Get all podcasts followed by a user."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, user_id, podcast_id, created_at
                    FROM podcast_followers
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                """, (user_id,))

                return [self._row_to_follower(row) for row in cursor.fetchall()]

    def get_by_podcast(self, podcast_id: str) -> List[PodcastFollower]:
        """Get all followers of a podcast."""
        with self._get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT id, user_id, podcast_id, created_at
                    FROM podcast_followers
                    WHERE podcast_id = %s
                    ORDER BY created_at DESC
                """, (podcast_id,))

                return [self._row_to_follower(row) for row in cursor.fetchall()]

    def count_by_podcast(self, podcast_id: str) -> int:
        """Count followers for a podcast."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM podcast_followers
                    WHERE podcast_id = %s
                """, (podcast_id,))

                row = cursor.fetchone()
                return row[0] if row else 0

    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """Get IDs of podcasts a user follows."""
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT podcast_id
                    FROM podcast_followers
                    WHERE user_id = %s
                """, (user_id,))

                return [row[0] for row in cursor.fetchall()]

    def _row_to_follower(self, row: dict) -> PodcastFollower:
        """Convert database row to PodcastFollower model."""
        return PodcastFollower(
            id=row["id"],
            user_id=row["user_id"],
            podcast_id=row["podcast_id"],
            created_at=row["created_at"],
        )
