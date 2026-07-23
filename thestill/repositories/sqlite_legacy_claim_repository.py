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

"""SQLite implementation of the spec #64 legacy-claim repository.

``BEGIN IMMEDIATE`` takes the writer lock up front, so a concurrent
claim/discard blocks until this transaction commits or rolls back, then
re-reads and finds the local row gone (winner committed) or intact
(winner rolled back — safe retry). All statements run on one connection;
``utils.sqlite_ext.connect`` commits on success and rolls back on error.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .legacy_claim_repository import LegacyClaimRepository, LegacyClaimResult

logger = get_logger(__name__)


class SqliteLegacyClaimRepository(LegacyClaimRepository):
    """SQLite-backed atomic claim/discard of the local account."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    @contextmanager
    def _locked_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            # Writer lock up front: serializes concurrent claim attempts.
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    @staticmethod
    def _local_user_id(conn: sqlite3.Connection, local_email: str) -> Optional[str]:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (local_email,)).fetchone()
        return row["id"] if row else None

    @staticmethod
    def _counts(conn: sqlite3.Connection, user_id: str) -> Dict[str, int]:
        def one(sql: str) -> int:
            return conn.execute(sql, (user_id,)).fetchone()[0]

        return {
            "followers": one("SELECT COUNT(*) FROM podcast_followers WHERE user_id = ?"),
            "inbox": one("SELECT COUNT(*) FROM user_episode_inbox WHERE user_id = ?"),
            "briefings": one("SELECT COUNT(*) FROM user_briefings WHERE user_id = ?"),
            "schedule": one("SELECT COUNT(*) FROM user_briefing_schedules WHERE user_id = ?"),
        }

    def claim_local_account(self, *, local_email: str, target_user_id: str, dry_run: bool = False) -> LegacyClaimResult:
        with self._locked_connection() as conn:
            local_id = self._local_user_id(conn, local_email)
            if local_id is None:
                return LegacyClaimResult(found=False, claimed=False)
            counts = self._counts(conn, local_id)
            if dry_run or local_id == target_user_id:
                return LegacyClaimResult(found=True, claimed=False, counts=counts)

            moved: Dict[str, int] = {}
            moved["followers"] = conn.execute(
                """
                UPDATE podcast_followers SET user_id = :to
                WHERE user_id = :frm
                  AND NOT EXISTS (
                    SELECT 1 FROM podcast_followers x
                    WHERE x.user_id = :to AND x.podcast_id = podcast_followers.podcast_id
                  )
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            moved["inbox"] = conn.execute(
                """
                UPDATE user_episode_inbox SET user_id = :to
                WHERE user_id = :frm
                  AND NOT EXISTS (
                    SELECT 1 FROM user_episode_inbox x
                    WHERE x.user_id = :to AND x.episode_id = user_episode_inbox.episode_id
                  )
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            moved["briefings"] = conn.execute(
                "UPDATE user_briefings SET user_id = ? WHERE user_id = ?",
                (target_user_id, local_id),
            ).rowcount
            moved["schedule"] = conn.execute(
                """
                UPDATE user_briefing_schedules SET user_id = :to
                WHERE user_id = :frm
                  AND NOT EXISTS (SELECT 1 FROM user_briefing_schedules WHERE user_id = :to)
                """,
                {"to": target_user_id, "frm": local_id},
            ).rowcount
            # The local operator was the admin; the claimant inherits that.
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (target_user_id,))
            # DELETE LAST — cascades away conflict rows deliberately left
            # behind above, and marks the claim durably done.
            conn.execute("DELETE FROM users WHERE id = ?", (local_id,))

            logger.info(
                "legacy_claim_transferred",
                target_user_id=target_user_id,
                **{f"moved_{k}": v for k, v in moved.items()},
            )
            return LegacyClaimResult(found=True, claimed=True, counts=moved)

    def discard_local_account(self, *, local_email: str, dry_run: bool = False) -> LegacyClaimResult:
        with self._locked_connection() as conn:
            local_id = self._local_user_id(conn, local_email)
            if local_id is None:
                return LegacyClaimResult(found=False, claimed=False)
            counts = self._counts(conn, local_id)
            if dry_run:
                return LegacyClaimResult(found=True, claimed=False, counts=counts)
            conn.execute("DELETE FROM users WHERE id = ?", (local_id,))
            logger.info("legacy_claim_discarded", **{f"discarded_{k}": v for k, v in counts.items()})
            return LegacyClaimResult(found=True, claimed=True, counts=counts)
