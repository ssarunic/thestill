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

"""PostgreSQL implementation of the spec #64 legacy-claim repository.

Faithful port of the SQLite implementation. ``SELECT ... FOR UPDATE`` on
the local ``users`` row provides the row lock the SQLite side gets from
``BEGIN IMMEDIATE``: a concurrent claim blocks on the lock, then sees
zero rows after the winner's commit. psycopg's connection context
manager commits on clean exit and rolls back on exception, so the whole
operation is all-or-nothing.
"""

from typing import Dict, Optional

import psycopg
from structlog import get_logger

from ..utils.postgres_ext import as_str, connect
from .legacy_claim_repository import LegacyClaimRepository, LegacyClaimResult

logger = get_logger(__name__)


class PostgresLegacyClaimRepository(LegacyClaimRepository):
    """Postgres-backed atomic claim/discard of the local account."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    @staticmethod
    def _lock_local_user_id(conn: psycopg.Connection, local_email: str) -> Optional[str]:
        row = conn.execute(
            "SELECT id FROM users WHERE email = %s FOR UPDATE",
            (local_email,),
        ).fetchone()
        return as_str(row["id"]) if row else None

    @staticmethod
    def _counts(conn: psycopg.Connection, user_id: str) -> Dict[str, int]:
        def one(sql: str) -> int:
            return conn.execute(sql, (user_id,)).fetchone()["n"]

        return {
            "followers": one("SELECT COUNT(*) AS n FROM podcast_followers WHERE user_id = %s"),
            "inbox": one("SELECT COUNT(*) AS n FROM user_episode_inbox WHERE user_id = %s"),
            "briefings": one("SELECT COUNT(*) AS n FROM user_briefings WHERE user_id = %s"),
            "schedule": one("SELECT COUNT(*) AS n FROM user_briefing_schedules WHERE user_id = %s"),
        }

    def claim_local_account(self, *, local_email: str, target_user_id: str, dry_run: bool = False) -> LegacyClaimResult:
        with connect(self.dsn) as conn:
            local_id = self._lock_local_user_id(conn, local_email)
            if local_id is None:
                return LegacyClaimResult(found=False, claimed=False)
            counts = self._counts(conn, local_id)
            if dry_run or local_id == target_user_id:
                return LegacyClaimResult(found=True, claimed=False, counts=counts)

            moved: Dict[str, int] = {}
            moved["followers"] = conn.execute(
                """
                UPDATE podcast_followers SET user_id = %(to)s
                WHERE user_id = %(frm)s
                  AND NOT EXISTS (
                    SELECT 1 FROM podcast_followers x
                    WHERE x.user_id = %(to)s AND x.podcast_id = podcast_followers.podcast_id
                  )
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            moved["inbox"] = conn.execute(
                """
                UPDATE user_episode_inbox SET user_id = %(to)s
                WHERE user_id = %(frm)s
                  AND NOT EXISTS (
                    SELECT 1 FROM user_episode_inbox x
                    WHERE x.user_id = %(to)s AND x.episode_id = user_episode_inbox.episode_id
                  )
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            moved["briefings"] = conn.execute(
                "UPDATE user_briefings SET user_id = %s WHERE user_id = %s",
                (target_user_id, local_id),
            ).rowcount
            moved["schedule"] = conn.execute(
                """
                UPDATE user_briefing_schedules SET user_id = %(to)s
                WHERE user_id = %(frm)s
                  AND NOT EXISTS (SELECT 1 FROM user_briefing_schedules WHERE user_id = %(to)s)
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            # The local operator was the admin; the claimant inherits that.
            conn.execute("UPDATE users SET is_admin = true WHERE id = %s", (target_user_id,))
            # DELETE LAST — cascades away conflict rows deliberately left
            # behind above, and marks the claim durably done.
            conn.execute("DELETE FROM users WHERE id = %s", (local_id,))

            logger.info(
                "legacy_claim_transferred",
                target_user_id=target_user_id,
                **{f"moved_{k}": v for k, v in moved.items()},
            )
            return LegacyClaimResult(found=True, claimed=True, counts=moved)

    def discard_local_account(self, *, local_email: str, dry_run: bool = False) -> LegacyClaimResult:
        with connect(self.dsn) as conn:
            local_id = self._lock_local_user_id(conn, local_email)
            if local_id is None:
                return LegacyClaimResult(found=False, claimed=False)
            counts = self._counts(conn, local_id)
            if dry_run:
                return LegacyClaimResult(found=True, claimed=False, counts=counts)
            conn.execute("DELETE FROM users WHERE id = %s", (local_id,))
            logger.info("legacy_claim_discarded", **{f"discarded_{k}": v for k, v in counts.items()})
            return LegacyClaimResult(found=True, claimed=True, counts=counts)
