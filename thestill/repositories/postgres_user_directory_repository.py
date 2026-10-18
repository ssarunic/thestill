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

"""PostgreSQL implementation of the spec #104 user directory."""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..models.user import UserStatus
from ..utils.postgres_ext import connect
from .postgres_user_repository import SELECT_COLS, row_to_user
from .user_directory_repository import USER_SORT_ORDER, UserDirectoryRepository, UserSort, UserSummary, search_pattern


def _where(status: Optional[UserStatus], q: Optional[str]) -> Tuple[str, list]:
    clauses, params = [], []
    if status is not None:
        clauses.append("u.status = %s")
        params.append(UserStatus(status).value)
    if q:
        clauses.append("(lower(u.email) LIKE %s ESCAPE '\\' OR lower(coalesce(u.name, '')) LIKE %s ESCAPE '\\')")
        params.extend([search_pattern(q)] * 2)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


class PostgresUserDirectoryRepository(UserDirectoryRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn

    def list_users(
        self,
        *,
        status: Optional[UserStatus] = None,
        q: Optional[str] = None,
        sort: UserSort = "created",
        limit: int = 50,
        offset: int = 0,
    ) -> List[UserSummary]:
        where, params = _where(status, q)
        cols = ", ".join(f"u.{c.strip()}" for c in SELECT_COLS.split(","))
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {cols},
                    (SELECT COUNT(*) FROM podcast_followers f WHERE f.user_id = u.id) AS follows,
                    (SELECT COUNT(*) FROM suspended_follows s WHERE s.user_id = u.id) AS suspended
                FROM users u {where}
                ORDER BY {USER_SORT_ORDER[sort]}
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            ).fetchall()
        return [UserSummary(user=row_to_user(r), follows=r["follows"], suspended_follows=r["suspended"]) for r in rows]

    def count_users(self, *, status: Optional[UserStatus] = None, q: Optional[str] = None) -> int:
        where, params = _where(status, q)
        with connect(self.dsn) as conn:
            return conn.execute(f"SELECT COUNT(*) AS n FROM users u {where}", params).fetchone()["n"]

    def count_admins(self) -> int:
        with connect(self.dsn) as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin AND status = 'active'").fetchone()["n"]
