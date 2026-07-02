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

"""PostgreSQL implementation of the podcast follower repository (spec #44).

Port of ``SqlitePodcastFollowerRepository`` behind the shared ABC, following
the conventions documented in ``utils.postgres_ext``: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema lives in
``postgres_schema.py``.
"""

from __future__ import annotations

from typing import List

from structlog import get_logger

from ..models.user import PodcastFollower
from ..utils.postgres_ext import as_str, connect
from .podcast_follower_repository import PodcastFollowerRepository

logger = get_logger(__name__)


class PostgresPodcastFollowerRepository(PodcastFollowerRepository):
    """PostgreSQL-backed follower repository. Connection-per-operation."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres podcast follower repository")

    def add(self, follower: PodcastFollower) -> PodcastFollower:
        """Add a follower relationship."""
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO podcast_followers (id, user_id, podcast_id, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (follower.id, follower.user_id, follower.podcast_id, follower.created_at),
            )
            logger.debug("Added follower", user_id=follower.user_id, podcast_id=follower.podcast_id)
            return follower

    def remove(self, user_id: str, podcast_id: str) -> bool:
        """Remove a follower relationship."""
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "DELETE FROM podcast_followers WHERE user_id = %s AND podcast_id = %s",
                (user_id, podcast_id),
            )
            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug("Removed follower", user_id=user_id, podcast_id=podcast_id)
            return deleted

    def exists(self, user_id: str, podcast_id: str) -> bool:
        """Check if a follower relationship exists."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT 1 FROM podcast_followers WHERE user_id = %s AND podcast_id = %s",
                (user_id, podcast_id),
            ).fetchone()
            return row is not None

    def get_by_user(self, user_id: str) -> List[PodcastFollower]:
        """Get all podcasts followed by a user."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, podcast_id, created_at
                FROM podcast_followers
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [self._row_to_follower(row) for row in rows]

    def get_by_podcast(self, podcast_id: str) -> List[PodcastFollower]:
        """Get all followers of a podcast."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, podcast_id, created_at
                FROM podcast_followers
                WHERE podcast_id = %s
                ORDER BY created_at DESC
                """,
                (podcast_id,),
            ).fetchall()
            return [self._row_to_follower(row) for row in rows]

    def count_by_podcast(self, podcast_id: str) -> int:
        """Count followers for a podcast."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM podcast_followers WHERE podcast_id = %s",
                (podcast_id,),
            ).fetchone()
            return row["count"] if row else 0

    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """Get IDs of podcasts a user follows."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT podcast_id FROM podcast_followers WHERE user_id = %s",
                (user_id,),
            ).fetchall()
            return [as_str(row["podcast_id"]) for row in rows]

    def get_follower_user_ids(self, podcast_id: str) -> List[str]:
        """Get IDs of users that follow a podcast."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT user_id FROM podcast_followers WHERE podcast_id = %s",
                (podcast_id,),
            ).fetchall()
            return [as_str(row["user_id"]) for row in rows]

    def _row_to_follower(self, row: dict) -> PodcastFollower:
        """Convert a dict row to PodcastFollower. timestamptz reads back as a
        tz-aware datetime; uuid columns stringify via as_str."""
        return PodcastFollower(
            id=as_str(row["id"]),
            user_id=as_str(row["user_id"]),
            podcast_id=as_str(row["podcast_id"]),
            created_at=row["created_at"],
        )
