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
from typing import Iterator, List

from structlog import get_logger

from ..models.user import PodcastFollower
from ..utils.sqlite_ext import connect
from .podcast_follower_repository import FollowDecider, FollowFacts, FollowOutcome, PodcastFollowerRepository

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
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        """Tuned SQLite connection. See ``thestill.utils.sqlite_ext.connect``."""
        with connect(self.db_path) as conn:
            yield conn

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

    def follow_with_gate(self, follower: PodcastFollower, decide: FollowDecider) -> FollowOutcome:
        refusal = None
        with self._get_connection() as conn:
            # Serialises every writer on the database, which covers the
            # per-podcast (and Phase 2 per-user) lock in one step.
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM podcast_followers WHERE user_id = ? AND podcast_id = ?",
                (follower.user_id, follower.podcast_id),
            ).fetchone():
                return FollowOutcome(follower=None, already_following=True)
            facts = FollowFacts(
                podcast_followers=conn.execute(
                    "SELECT COUNT(*) FROM podcast_followers WHERE podcast_id = ?", (follower.podcast_id,)
                ).fetchone()[0],
                user_follows_total=conn.execute(
                    "SELECT COUNT(*) FROM podcast_followers WHERE user_id = ?", (follower.user_id,)
                ).fetchone()[0],
            )
            refusal = decide(facts).refusal
            if refusal is None:
                conn.execute(
                    "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
                    (follower.id, follower.user_id, follower.podcast_id, follower.created_at.isoformat()),
                )
        if refusal is not None:
            raise refusal
        return FollowOutcome(follower=follower, introduced=facts.podcast_followers == 0)

    def introduced_podcast_ids(self, user_id: str) -> List[str]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                WITH f AS (
                    SELECT user_id, podcast_id, created_at FROM podcast_followers
                    UNION ALL
                    SELECT user_id, podcast_id, created_at FROM suspended_follows
                ), first AS (
                    SELECT podcast_id, MIN(created_at) AS first_at FROM f GROUP BY podcast_id
                )
                SELECT DISTINCT f.podcast_id
                FROM f JOIN first ON first.podcast_id = f.podcast_id AND f.created_at = first.first_at
                WHERE f.user_id = ?
                """,
                (user_id,),
            ).fetchall()
            return [row["podcast_id"] for row in rows]

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

    def get_follower_user_ids(self, podcast_id: str) -> List[str]:
        """Get IDs of users that follow a podcast."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT user_id
                FROM podcast_followers
                WHERE podcast_id = ?
                """,
                (podcast_id,),
            )

            return [row["user_id"] for row in cursor.fetchall()]

    def _row_to_follower(self, row: sqlite3.Row) -> PodcastFollower:
        """Convert database row to PodcastFollower model."""
        return PodcastFollower(
            id=row["id"],
            user_id=row["user_id"],
            podcast_id=row["podcast_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
