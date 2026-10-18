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

"""PostgreSQL implementation of the spec #104 audit log repository."""

from __future__ import annotations

from typing import List, Optional, Tuple

from psycopg.types.json import Jsonb

from ..utils.postgres_ext import as_str, connect
from .audit_log_repository import AuditEntry, AuditLogRepository


def _where(action: Optional[str], target_user_id: Optional[str]) -> Tuple[str, list]:
    clauses, params = [], []
    if action:
        clauses.append("action = %s")
        params.append(action)
    if target_user_id:
        clauses.append("target_user_id = %s")
        params.append(target_user_id)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


class PostgresAuditLogRepository(AuditLogRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn

    def append(self, entry: AuditEntry) -> None:
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log (created_at, actor_user_id, actor, action, target_user_id, details)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.created_at,
                    entry.actor_user_id,
                    entry.actor,
                    entry.action,
                    entry.target_user_id,
                    Jsonb(entry.details),
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
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT id, created_at, actor_user_id, actor, action, target_user_id, details
                FROM admin_audit_log {where}
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit, offset),
            ).fetchall()
        return [
            AuditEntry(
                id=row["id"],
                created_at=row["created_at"],
                actor_user_id=as_str(row["actor_user_id"]),
                actor=row["actor"],
                action=row["action"],
                target_user_id=as_str(row["target_user_id"]),
                details=row["details"],
            )
            for row in rows
        ]

    def count(self, *, action: Optional[str] = None, target_user_id: Optional[str] = None) -> int:
        where, params = _where(action, target_user_id)
        with connect(self.dsn) as conn:
            return conn.execute(f"SELECT COUNT(*) AS n FROM admin_audit_log {where}", params).fetchone()["n"]
