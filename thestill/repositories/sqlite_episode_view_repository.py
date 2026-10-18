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

"""SQLite implementation of ``EpisodeViewRepository`` (spec #103).

SQLite installs have no analytics; the table is kept so both backends
store the same facts (FM-6). Timestamps are ISO-8601 with ``+00:00``.
"""

from datetime import datetime
from pathlib import Path

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .episode_view_repository import EpisodeViewRepository

logger = get_logger(__name__)


class SqliteEpisodeViewRepository(EpisodeViewRepository):
    """SQLite-backed first-view store."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite episode view repository", db_path=str(self.db_path))

    def record_view(self, user_id: str, episode_id: str, at: datetime) -> bool:
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO user_episode_views (user_id, episode_id, first_viewed_at)
                SELECT ?, id, ? FROM episodes WHERE id = ?
                ON CONFLICT (user_id, episode_id) DO NOTHING
                """,
                (user_id, at.isoformat(), episode_id),
            )
            return cursor.rowcount == 1
