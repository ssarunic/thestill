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

"""SQLite implementation of the spec #104 user directory."""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..models.user import User, UserStatus
from ..utils.sqlite_ext import connect
from .sqlite_user_repository import SELECT_COLS, row_to_user
from .user_directory_repository import (
    USER_SORT_ORDER,
    UserDirectoryRepository,
    UserSort,
    UserSummary,
    listing_where,
    summary_columns,
    summary_from_row,
)


def _where(status: Optional[UserStatus], q: Optional[str]) -> Tuple[str, list]:
    return listing_where("?", status, q)


class SqliteUserDirectoryRepository(UserDirectoryRepository):
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def list_users(
        self,
        *,
        status: Optional[UserStatus] = None,
        q: Optional[str] = None,
        sort: UserSort = "created",
        limit: int = 50,
        offset: int = 0,
        usage_since: Optional[datetime] = None,
    ) -> List[UserSummary]:
        where, params = _where(status, q)
        cols = ", ".join(f"u.{c.strip()}" for c in SELECT_COLS.split(","))
        counts, count_params = summary_columns("?", usage_since)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT {cols},
                    {counts}
                FROM users u {where}
                ORDER BY {USER_SORT_ORDER[sort]}
                LIMIT ? OFFSET ?
                """,
                (*[s.isoformat() for s in count_params], *params, limit, offset),
            ).fetchall()
        return [summary_from_row(r, row_to_user(r)) for r in rows]

    def count_users(self, *, status: Optional[UserStatus] = None, q: Optional[str] = None) -> int:
        where, params = _where(status, q)
        with connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM users u {where}", params).fetchone()[0]

    def created_since(self, since: datetime) -> List[User]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT {SELECT_COLS} FROM users WHERE created_at >= ? ORDER BY created_at DESC",
                (since.isoformat(),),
            ).fetchall()
        return [row_to_user(row) for row in rows]

    def emails_by_id(self, user_ids: Sequence[str]) -> Dict[str, str]:
        ids = list(dict.fromkeys(i for i in user_ids if i))
        if not ids:
            return {}
        with connect(self.db_path) as conn:
            rows = conn.execute(f"SELECT id, email FROM users WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return {row["id"]: row["email"] for row in rows}

    def count_admins(self) -> int:
        with connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND status = 'active'").fetchone()[0]
