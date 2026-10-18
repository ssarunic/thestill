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

"""PostgreSQL implementation of the spec #104 usage ledger."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

import psycopg

from ..utils.postgres_ext import connect
from .usage_event_repository import (
    COUNTED_SQL,
    PRUNE_KEEP_KINDS,
    ReserveResult,
    UsageEventRepository,
    UsageRecord,
    usage_lock_key,
)


def lock_usage(conn: psycopg.Connection, user_id: str, lock_group: str) -> None:
    """Per-(user, group) transaction lock; taken before any podcast lock."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (usage_lock_key(user_id, lock_group),))


def count_usage(conn: psycopg.Connection, user_id: str, kind: str, since: datetime) -> Tuple[int, Optional[datetime]]:
    row = conn.execute(
        f"SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM usage_events"
        f" WHERE user_id = %s AND kind = %s AND {COUNTED_SQL} AND created_at >= %s",
        (user_id, kind, since),
    ).fetchone()
    return row["n"], row["oldest"]


def insert_usage(conn: psycopg.Connection, user_id: str, records: Sequence[UsageRecord], now: datetime) -> list[str]:
    ids = []
    for rec in records:
        event_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO usage_events (id, user_id, kind, state, created_at, settled_at, subject_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (event_id, user_id, rec.kind, rec.state, now, None if rec.state == "reserved" else now, rec.subject_id),
        )
        ids.append(event_id)
    return ids


class PostgresUsageEventRepository(UsageEventRepository):
    def __init__(self, dsn: str):
        self.dsn = dsn

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
        with connect(self.dsn) as conn:
            lock_usage(conn, user_id, lock_group)
            count, _oldest = count_usage(conn, user_id, kind, since)
            if limit and count >= limit:
                insert_usage(conn, user_id, [UsageRecord("quota_exceeded", subject_id=quota)], now)
                # Rows leave oldest first; the (count - limit + 1)th oldest
                # leaving brings the count under the limit.
                row = conn.execute(
                    f"SELECT created_at FROM usage_events WHERE user_id = %s AND kind = %s AND {COUNTED_SQL}"
                    " AND created_at >= %s ORDER BY created_at LIMIT 1 OFFSET %s",
                    (user_id, kind, since, count - limit),
                ).fetchone()
                return ReserveResult(None, count, row["created_at"] if row else None)
            [event_id] = insert_usage(conn, user_id, [UsageRecord(kind, subject_id, "reserved")], now)
            return ReserveResult(event_id, count, None)

    def settle(self, event_id: str, state: str, *, now: datetime, subject_id: Optional[str] = None) -> bool:
        with connect(self.dsn) as conn:
            return (
                conn.execute(
                    "UPDATE usage_events SET state = %s, settled_at = %s, subject_id = COALESCE(%s, subject_id)"
                    " WHERE id = %s AND state = 'reserved'",
                    (state, now, subject_id, event_id),
                ).rowcount
                > 0
            )

    def record(self, user_id: str, records: Sequence[UsageRecord], *, now: datetime) -> None:
        with connect(self.dsn) as conn:
            insert_usage(conn, user_id, records, now)

    def window(self, user_id: str, kind: str, since: datetime) -> Tuple[int, Optional[datetime]]:
        with connect(self.dsn) as conn:
            return count_usage(conn, user_id, kind, since)

    def counts_by_kind(self, user_id: str, since: datetime) -> Dict[str, int]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT kind, COUNT(*) AS n FROM usage_events WHERE user_id = %s AND {COUNTED_SQL}"
                " AND created_at >= %s GROUP BY kind",
                (user_id, since),
            ).fetchall()
        return {row["kind"]: row["n"] for row in rows}

    def release_stale(self, older_than: datetime, *, now: datetime) -> int:
        with connect(self.dsn) as conn:
            return conn.execute(
                "UPDATE usage_events SET state = 'released', settled_at = %s"
                " WHERE state = 'reserved' AND created_at < %s",
                (now, older_than),
            ).rowcount

    def prune(self, older_than: datetime, *, keep_kinds: Sequence[str] = PRUNE_KEEP_KINDS) -> int:
        with connect(self.dsn) as conn:
            return conn.execute(
                "DELETE FROM usage_events WHERE created_at < %s AND state != 'reserved'" " AND NOT (kind = ANY(%s))",
                (older_than, list(keep_kinds)),
            ).rowcount
