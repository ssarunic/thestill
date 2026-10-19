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

"""PostgreSQL implementation of the spec #104 suspended-follow repository."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable, List

from ..utils.postgres_ext import as_str, connect
from .suspended_follow_repository import SuspendedFollowRepository


class PostgresSuspendedFollowRepository(SuspendedFollowRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn

    def move_follows_aside(self, user_id: str, *, now: datetime) -> List[str]:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO suspended_follows (user_id, podcast_id, created_at, suspended_at)
                SELECT user_id, podcast_id, created_at, %s FROM podcast_followers WHERE user_id = %s
                ON CONFLICT (user_id, podcast_id) DO NOTHING
                """,
                (now, user_id),
            )
            rows = conn.execute(
                "DELETE FROM podcast_followers WHERE user_id = %s RETURNING podcast_id", (user_id,)
            ).fetchall()
        return [as_str(row["podcast_id"]) for row in rows]

    def restore_follows(self, user_id: str) -> List[str]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "DELETE FROM suspended_follows WHERE user_id = %s RETURNING podcast_id, created_at", (user_id,)
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, podcast_id) DO NOTHING
                    """,
                    (str(uuid.uuid4()), user_id, row["podcast_id"], row["created_at"]),
                )
        return [as_str(row["podcast_id"]) for row in rows]

    def discard(self, user_id: str, podcast_ids: Iterable[str]) -> int:
        ids = list(podcast_ids)
        if not ids:
            return 0
        with connect(self.dsn) as conn:
            return conn.execute(
                "DELETE FROM suspended_follows WHERE user_id = %s AND podcast_id = ANY(%s::uuid[])", (user_id, ids)
            ).rowcount

    def list_podcast_ids(self, user_id: str) -> List[str]:
        with connect(self.dsn) as conn:
            rows = conn.execute("SELECT podcast_id FROM suspended_follows WHERE user_id = %s", (user_id,)).fetchall()
        return [as_str(row["podcast_id"]) for row in rows]
