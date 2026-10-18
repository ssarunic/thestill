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

"""PostgreSQL implementation of ``EpisodeViewRepository`` (spec #103).

Schema in ``postgres_schema.py`` and alembic ``0019_episode_views_listened_at``.
"""

from __future__ import annotations

from datetime import datetime

from structlog import get_logger

from ..utils.ids import is_uuid
from ..utils.postgres_ext import connect
from .episode_view_repository import EpisodeViewRepository

logger = get_logger(__name__)


class PostgresEpisodeViewRepository(EpisodeViewRepository):
    """PostgreSQL-backed first-view store."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres episode view repository")

    def record_view(self, user_id: str, episode_id: str, at: datetime) -> bool:
        if not (is_uuid(user_id) and is_uuid(episode_id)):
            return False
        # Selecting from episodes makes an unknown id a no-op, not an FK error.
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_episode_views (user_id, episode_id, first_viewed_at)
                SELECT %s, id, %s FROM episodes WHERE id = %s
                ON CONFLICT (user_id, episode_id) DO NOTHING
                """,
                (user_id, at, episode_id),
            )
            return cursor.rowcount == 1
