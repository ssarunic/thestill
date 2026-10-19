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

"""SQLite implementation of the spec #104 usage ledger.

``BEGIN IMMEDIATE`` serialises every writer on the database, which stands
in for Postgres's per-(user, group) advisory lock — acceptable for a
handful of costly actions a minute.
"""

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

from ..utils.sqlite_ext import connect
from .usage_event_repository import COUNTED_SQL, PRUNE_KEEP_KINDS, ReserveResult, UsageEventRepository, UsageRecord


def count_usage(conn: sqlite3.Connection, user_id: str, kind: str, since: datetime) -> Tuple[int, Optional[datetime]]:
    """Counted rows of ``kind`` since ``since``, and the oldest one's time."""
    row = conn.execute(
        f"SELECT COUNT(*), MIN(created_at) FROM usage_events"
        f" WHERE user_id = ? AND kind = ? AND {COUNTED_SQL} AND created_at >= ?",
        (user_id, kind, since.isoformat()),
    ).fetchone()
    return row[0], datetime.fromisoformat(row[1]) if row[1] else None


def insert_usage(conn: sqlite3.Connection, user_id: str, records: Sequence[UsageRecord], now: datetime) -> list[str]:
    ids = []
    for rec in records:
        event_id = str(uuid.uuid4())
        settled = None if rec.state == "reserved" else now.isoformat()
        conn.execute(
            "INSERT INTO usage_events (id, user_id, kind, state, created_at, settled_at, subject_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, user_id, rec.kind, rec.state, now.isoformat(), settled, rec.subject_id),
        )
        ids.append(event_id)
    return ids


class SqliteUsageEventRepository(UsageEventRepository):
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def reserve(
        self,
        user_id: str,
        kind: str,
        *,
        quota: str,
        limit: int,
        since: datetime,
        lock_group: str,
        subject_id: Optional[str],
        now: datetime,
    ) -> ReserveResult:
        with connect(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            count, _oldest = count_usage(conn, user_id, kind, since)
            if limit and count >= limit:
                insert_usage(conn, user_id, [UsageRecord("quota_exceeded", subject_id=quota)], now)
                # Rows leave oldest first; the (count - limit + 1)th oldest
                # leaving brings the count under the limit.
                row = conn.execute(
                    f"SELECT created_at FROM usage_events WHERE user_id = ? AND kind = ? AND {COUNTED_SQL}"
                    " AND created_at >= ? ORDER BY created_at LIMIT 1 OFFSET ?",
                    (user_id, kind, since.isoformat(), count - limit),
                ).fetchone()
                return ReserveResult(None, count, datetime.fromisoformat(row[0]) if row else None)
            [event_id] = insert_usage(conn, user_id, [UsageRecord(kind, subject_id, "reserved")], now)
            return ReserveResult(event_id, count, None)

    def settle(self, event_id: str, state: str, *, now: datetime, subject_id: Optional[str] = None) -> bool:
        with connect(self.db_path) as conn:
            return (
                conn.execute(
                    "UPDATE usage_events SET state = ?, settled_at = ?, subject_id = COALESCE(?, subject_id)"
                    " WHERE id = ? AND state = 'reserved'",
                    (state, now.isoformat(), subject_id, event_id),
                ).rowcount
                > 0
            )

    def record(self, user_id: str, records: Sequence[UsageRecord], *, now: datetime) -> None:
        with connect(self.db_path) as conn:
            insert_usage(conn, user_id, records, now)

    def window(self, user_id: str, kind: str, since: datetime) -> Tuple[int, Optional[datetime]]:
        with connect(self.db_path) as conn:
            return count_usage(conn, user_id, kind, since)

    def counts_by_kind(self, user_id: str, since: datetime) -> Dict[str, int]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT kind, COUNT(*) FROM usage_events WHERE user_id = ? AND {COUNTED_SQL} AND created_at >= ?"
                " GROUP BY kind",
                (user_id, since.isoformat()),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def daily_counts(self, user_id: str, since: datetime) -> Dict[str, Dict[str, int]]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT kind, substr(created_at, 1, 10) AS day, COUNT(*) FROM usage_events"
                f" WHERE user_id = ? AND {COUNTED_SQL} AND created_at >= ? GROUP BY kind, day",
                (user_id, since.isoformat()),
            ).fetchall()
        result: Dict[str, Dict[str, int]] = {}
        for kind, day, n in rows:
            result.setdefault(kind, {})[day] = n
        return result

    def counts_by_user(self, kind: str, since: datetime) -> Dict[str, int]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT user_id, COUNT(*) FROM usage_events WHERE kind = ? AND {COUNTED_SQL} AND created_at >= ?"
                " GROUP BY user_id",
                (kind, since.isoformat()),
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def release_stale(self, older_than: datetime, *, now: datetime) -> int:
        with connect(self.db_path) as conn:
            return conn.execute(
                "UPDATE usage_events SET state = 'released', settled_at = ?"
                " WHERE state = 'reserved' AND created_at < ?",
                (now.isoformat(), older_than.isoformat()),
            ).rowcount

    def prune(self, older_than: datetime, *, keep_kinds: Sequence[str] = PRUNE_KEEP_KINDS) -> int:
        kept = f" AND kind NOT IN ({','.join('?' * len(keep_kinds))})" if keep_kinds else ""
        with connect(self.db_path) as conn:
            return conn.execute(
                f"DELETE FROM usage_events WHERE created_at < ? AND state != 'reserved'{kept}",
                (older_than.isoformat(), *keep_kinds),
            ).rowcount
