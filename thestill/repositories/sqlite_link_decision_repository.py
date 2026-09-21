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

"""SQLite implementation of ``LinkDecisionRepository`` (spec #81)."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from structlog import get_logger

from ..utils.sqlite_ext import connect
from .link_decision_repository import LinkDecisionRepository, StoredLinkDecision

logger = get_logger(__name__)

_COLS = "surface_key, podcast_id, qid, confidence, reason, decided_at, linker_version, hits"

# The conflict target must repeat the partial index's predicate, so each
# scope has its own statement.
_UPSERT = """
    INSERT INTO entity_link_decisions (surface_key, podcast_id, qid, confidence, reason, decided_at, linker_version)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT {target} DO UPDATE SET
        qid = excluded.qid,
        confidence = excluded.confidence,
        reason = excluded.reason,
        decided_at = excluded.decided_at,
        linker_version = excluded.linker_version
"""
_UPSERT_PODCAST = _UPSERT.format(target="(surface_key, podcast_id) WHERE podcast_id IS NOT NULL")
_UPSERT_CORPUS = _UPSERT.format(target="(surface_key) WHERE podcast_id IS NULL")


class SqliteLinkDecisionRepository(LinkDecisionRepository):
    """SQLite-backed decision cache. Timestamps are tz-aware ISO-8601
    strings (``+00:00``), the project convention."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.info("Initialized SQLite link decision repository", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self) -> Iterator[sqlite3.Connection]:
        with connect(self.db_path) as conn:
            yield conn

    def get(self, surface_key: str, podcast_id: Optional[str]) -> Optional[StoredLinkDecision]:
        scope, params = _scope(podcast_id)
        with self._get_connection() as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM entity_link_decisions WHERE surface_key = ? AND {scope}",
                (surface_key, *params),
            ).fetchone()
            return self._row_to_decision(row) if row else None

    def upsert(self, decision: StoredLinkDecision) -> None:
        sql = _UPSERT_CORPUS if decision.podcast_id is None else _UPSERT_PODCAST
        with self._get_connection() as conn:
            conn.execute(
                sql,
                (
                    decision.surface_key,
                    decision.podcast_id,
                    decision.qid,
                    decision.confidence,
                    decision.reason,
                    decision.decided_at.isoformat(),
                    decision.linker_version,
                ),
            )

    def record_hit(self, surface_key: str, podcast_id: Optional[str]) -> None:
        scope, params = _scope(podcast_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE entity_link_decisions SET hits = hits + 1 WHERE surface_key = ? AND {scope}",
                (surface_key, *params),
            )

    def podcast_decisions(self, surface_key: str) -> List[StoredLinkDecision]:
        with self._get_connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM entity_link_decisions WHERE surface_key = ? AND podcast_id IS NOT NULL",
                (surface_key,),
            ).fetchall()
            return [self._row_to_decision(row) for row in rows]

    def delete(self, surface_key: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM entity_link_decisions WHERE surface_key = ?", (surface_key,))
            return cursor.rowcount

    @staticmethod
    def _row_to_decision(row) -> StoredLinkDecision:
        return StoredLinkDecision(
            surface_key=row["surface_key"],
            podcast_id=row["podcast_id"],
            qid=row["qid"],
            confidence=row["confidence"],
            reason=row["reason"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            linker_version=row["linker_version"],
            hits=row["hits"],
        )


def _scope(podcast_id: Optional[str]) -> Tuple[str, tuple]:
    if podcast_id is None:
        return "podcast_id IS NULL", ()
    return "podcast_id = ?", (podcast_id,)
