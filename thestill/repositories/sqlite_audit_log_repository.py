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

"""SQLite implementation of the spec #104 audit log repository."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from ..utils.sqlite_ext import connect
from .audit_log_repository import AuditEntry, AuditLogRepository


def _where(action: Optional[str], target_user_id: Optional[str]) -> Tuple[str, list]:
    clauses, params = [], []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if target_user_id:
        clauses.append("target_user_id = ?")
        params.append(target_user_id)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


class SqliteAuditLogRepository(AuditLogRepository):
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def append(self, entry: AuditEntry) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log (created_at, actor_user_id, actor, action, target_user_id, details)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.created_at.isoformat(),
                    entry.actor_user_id,
                    entry.actor,
                    entry.action,
                    entry.target_user_id,
                    json.dumps(entry.details),
                ),
            )

    def list(
        self,
        *,
        action: Optional[str] = None,
        target_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AuditEntry]:
        where, params = _where(action, target_user_id)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, created_at, actor_user_id, actor, action, target_user_id, details
                FROM admin_audit_log {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return [
            AuditEntry(
                id=row["id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                actor_user_id=row["actor_user_id"],
                actor=row["actor"],
                action=row["action"],
                target_user_id=row["target_user_id"],
                details=json.loads(row["details"]),
            )
            for row in rows
        ]

    def count(self, *, action: Optional[str] = None, target_user_id: Optional[str] = None) -> int:
        where, params = _where(action, target_user_id)
        with connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM admin_audit_log {where}", params).fetchone()[0]
