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

"""SQLite implementation of the spec #104 suspended-follow repository."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from ..utils.sqlite_ext import connect
from .suspended_follow_repository import SuspendedFollowRepository


class SqliteSuspendedFollowRepository(SuspendedFollowRepository):
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def move_follows_aside(self, user_id: str, *, now: datetime) -> List[str]:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            ids = [
                row["podcast_id"]
                for row in conn.execute("SELECT podcast_id FROM podcast_followers WHERE user_id = ?", (user_id,))
            ]
            conn.execute(
                """
                INSERT INTO suspended_follows (user_id, podcast_id, created_at, suspended_at)
                SELECT user_id, podcast_id, created_at, ? FROM podcast_followers WHERE user_id = ?
                ON CONFLICT(user_id, podcast_id) DO NOTHING
                """,
                (now.isoformat(), user_id),
            )
            conn.execute("DELETE FROM podcast_followers WHERE user_id = ?", (user_id,))
        return ids

    def restore_follows(self, user_id: str) -> List[str]:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT podcast_id, created_at FROM suspended_follows WHERE user_id = ?", (user_id,)
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, podcast_id) DO NOTHING
                    """,
                    (str(uuid.uuid4()), user_id, row["podcast_id"], row["created_at"]),
                )
            conn.execute("DELETE FROM suspended_follows WHERE user_id = ?", (user_id,))
        return [row["podcast_id"] for row in rows]

    def discard(self, user_id: str, podcast_ids: Iterable[str]) -> int:
        ids = list(podcast_ids)
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with connect(self.db_path) as conn:
            return conn.execute(
                f"DELETE FROM suspended_follows WHERE user_id = ? AND podcast_id IN ({placeholders})",
                (user_id, *ids),
            ).rowcount

    def list_podcast_ids(self, user_id: str) -> List[str]:
        with connect(self.db_path) as conn:
            return [
                row["podcast_id"]
                for row in conn.execute("SELECT podcast_id FROM suspended_follows WHERE user_id = ?", (user_id,))
            ]
