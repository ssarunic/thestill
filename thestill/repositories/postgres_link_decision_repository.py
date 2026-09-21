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

"""PostgreSQL implementation of ``LinkDecisionRepository`` (spec #81).

Follows the ``utils.postgres_ext`` conventions: native ``uuid`` ids,
``timestamptz`` datetimes, ``%s`` placeholders. Schema in
``postgres_schema.py`` and alembic ``0011_entity_link_decisions``.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from structlog import get_logger

from ..utils.postgres_ext import as_str, connect
from .link_decision_repository import LinkDecisionRepository, StoredLinkDecision

logger = get_logger(__name__)

_COLS = "surface_key, podcast_id, qid, confidence, reason, decided_at, linker_version, hits"

# The conflict target must repeat the partial index's predicate, so each
# scope has its own statement.
_UPSERT = """
    INSERT INTO entity_link_decisions (surface_key, podcast_id, qid, confidence, reason, decided_at, linker_version)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT {target} DO UPDATE SET
        qid = EXCLUDED.qid,
        confidence = EXCLUDED.confidence,
        reason = EXCLUDED.reason,
        decided_at = EXCLUDED.decided_at,
        linker_version = EXCLUDED.linker_version
"""
_UPSERT_PODCAST = _UPSERT.format(target="(surface_key, podcast_id) WHERE podcast_id IS NOT NULL")
_UPSERT_CORPUS = _UPSERT.format(target="(surface_key) WHERE podcast_id IS NULL")


class PostgresLinkDecisionRepository(LinkDecisionRepository):
    """PostgreSQL-backed decision cache."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        logger.info("Initialized Postgres link decision repository")

    def get(self, surface_key: str, podcast_id: Optional[str]) -> Optional[StoredLinkDecision]:
        scope, params = _scope(podcast_id)
        with connect(self.dsn) as conn:
            row = conn.execute(
                f"SELECT {_COLS} FROM entity_link_decisions WHERE surface_key = %s AND {scope}",
                (surface_key, *params),
            ).fetchone()
            return self._row_to_decision(row) if row else None

    def upsert(self, decision: StoredLinkDecision) -> None:
        sql = _UPSERT_CORPUS if decision.podcast_id is None else _UPSERT_PODCAST
        with connect(self.dsn) as conn:
            conn.execute(
                sql,
                (
                    decision.surface_key,
                    decision.podcast_id,
                    decision.qid,
                    decision.confidence,
                    decision.reason,
                    decision.decided_at,
                    decision.linker_version,
                ),
            )

    def record_hit(self, surface_key: str, podcast_id: Optional[str]) -> None:
        scope, params = _scope(podcast_id)
        with connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE entity_link_decisions SET hits = hits + 1 WHERE surface_key = %s AND {scope}",
                (surface_key, *params),
            )

    def podcast_decisions(self, surface_key: str) -> List[StoredLinkDecision]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM entity_link_decisions WHERE surface_key = %s AND podcast_id IS NOT NULL",
                (surface_key,),
            ).fetchall()
            return [self._row_to_decision(row) for row in rows]

    def delete(self, surface_key: str) -> int:
        with connect(self.dsn) as conn:
            cursor = conn.execute("DELETE FROM entity_link_decisions WHERE surface_key = %s", (surface_key,))
            return cursor.rowcount

    @staticmethod
    def _row_to_decision(row) -> StoredLinkDecision:
        return StoredLinkDecision(
            surface_key=row["surface_key"],
            podcast_id=as_str(row["podcast_id"]) if row["podcast_id"] is not None else None,
            qid=row["qid"],
            confidence=row["confidence"],
            reason=row["reason"],
            decided_at=row["decided_at"],
            linker_version=row["linker_version"],
            hits=row["hits"],
        )


def _scope(podcast_id: Optional[str]) -> Tuple[str, tuple]:
    if podcast_id is None:
        return "podcast_id IS NULL", ()
    return "podcast_id = %s", (podcast_id,)
