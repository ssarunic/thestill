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

"""SQLite repository for the spec #28 entity layer.

The schema is created by ``SqlitePodcastRepository._run_migrations``
(which runs at app boot); this class operates on the same database
but does not own the DDL.

Phase 1.2-1.3 implements the mention-write path used by the
``extract-entities`` handler. The remaining methods (entity upsert,
resolution, find/list queries, co-occurrences) stay stubbed until
their respective Phase 1 sub-tasks land — keeping the boundary
explicit prevents accidental partial implementations.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from structlog import get_logger

from ..models.entities import EntityMention, EntityRecord

logger = get_logger(__name__)


class SqliteEntityRepository:
    """Phase-0 typed stub for ``entities`` / ``entity_mentions`` tables.

    The schema migration lives in ``SqlitePodcastRepository._run_migrations``
    and runs at ``Config`` init via ``database_path``; this class
    operates on the same database but does not own the DDL.

    Method bodies raise ``NotImplementedError`` so Phase 1 must fill
    them in before any caller can rely on them. The shape (parameter
    names, return types) is what the Phase 1 extractor / resolver / MCP
    tools will consume.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        logger.debug("SqliteEntityRepository initialized", db_path=str(self.db_path))

    @contextmanager
    def _get_connection(self):
        """Mirror of ``SqlitePodcastRepository._get_connection``.

        Same WAL pragmas as the podcast repo so the entity layer
        participates in the same concurrency story (multiple readers,
        single writer, ``busy_timeout=5000``).
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def upsert_entity(self, entity: EntityRecord) -> str:
        """Create or update an ``entities`` row, returning its id.

        Phase 1: insert when ``id`` is unseen; merge aliases / refresh
        ``updated_at`` when the same QID is already present.
        """
        raise NotImplementedError("Phase 1 — see spec #28 task 1.5")

    def get_entity(self, entity_id: str) -> Optional[EntityRecord]:
        """Look up by canonical ``"{type}:{slug}"`` id."""
        raise NotImplementedError("Phase 1 — see spec #28 task 1.8")

    def find_entity_by_qid(self, wikidata_qid: str) -> Optional[EntityRecord]:
        """Look up by Wikidata QID (used during resolution merging)."""
        raise NotImplementedError("Phase 1 — see spec #28 task 1.5")

    # ------------------------------------------------------------------
    # Mentions
    # ------------------------------------------------------------------

    def insert_mentions(self, mentions: Iterable[EntityMention]) -> int:
        """Bulk-insert ``entity_mentions`` rows; return rowcount.

        Called by ``extract-entities`` once per episode with rows whose
        ``entity_id`` is ``None`` and ``resolution_status`` is
        ``"pending"``. The ``id`` AUTOINCREMENT column is filled in by
        SQLite — input ``EntityMention.id`` (if any) is ignored.

        Empty input is a no-op that returns 0.
        """
        rows = [
            (
                m.entity_id,
                m.resolution_status.value,
                m.episode_id,
                m.segment_id,
                m.start_ms,
                m.end_ms,
                m.speaker,
                m.role.value if m.role else None,
                m.surface_form,
                m.quote_excerpt,
                m.sentiment,
                m.confidence,
                m.extractor,
                m.created_at.isoformat(),
                m.resolved_at.isoformat() if m.resolved_at else None,
            )
            for m in mentions
        ]
        if not rows:
            return 0
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO entity_mentions (
                    entity_id, resolution_status, episode_id, segment_id,
                    start_ms, end_ms, speaker, role, surface_form,
                    quote_excerpt, sentiment, confidence, extractor,
                    created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        logger.info("entity_mentions_inserted", count=len(rows))
        return len(rows)

    def delete_mentions_for_episode(self, episode_id: str) -> int:
        """Wipe all ``entity_mentions`` for one episode; return rowcount.

        Used by ``extract-entities`` to make the handler idempotent —
        re-running on an already-extracted episode replaces the old
        mentions wholesale rather than producing duplicates. Cascade
        also removes any resolved-mention pointers for that episode,
        which is fine because resolution will re-run from scratch
        anyway.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM entity_mentions WHERE episode_id = ?",
                (episode_id,),
            )
            count = cursor.rowcount
        if count:
            logger.info(
                "entity_mentions_deleted_for_episode",
                episode_id=episode_id,
                count=count,
            )
        return count

    def count_mentions_for_episode(self, episode_id: str) -> int:
        """Diagnostic — used by tests and ``thestill status``."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM entity_mentions WHERE episode_id = ?",
                (episode_id,),
            ).fetchone()
        return row[0] if row else 0

    def list_pending_mentions(
        self,
        *,
        episode_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[EntityMention]:
        """Return mentions with ``resolution_status='pending'``.

        Phase 1: drives the ``resolve-entities`` batching loop.
        """
        raise NotImplementedError("Phase 1 — see spec #28 task 1.5")

    def resolve_mention(
        self,
        *,
        mention_id: int,
        entity_id: Optional[str],
        status: str,
        resolved_at: Optional[datetime] = None,
    ) -> bool:
        """Flip a pending mention to ``resolved`` or ``unresolvable``."""
        raise NotImplementedError("Phase 1 — see spec #28 task 1.5")

    def find_mentions(
        self,
        *,
        entity_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_id: Optional[str] = None,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        role: Optional[str] = None,
        limit: int = 50,
    ) -> List[EntityMention]:
        """Backing query for the ``find_mentions`` MCP tool / CLI peer."""
        raise NotImplementedError("Phase 1 — see spec #28 task 1.8")

    def list_mentions_by_speaker(
        self,
        *,
        speaker: str,
        topic: Optional[str] = None,
        podcast_id: Optional[str] = None,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        limit: int = 50,
    ) -> List[EntityMention]:
        """Backing query for the ``list_quotes_by`` MCP tool."""
        raise NotImplementedError("Phase 1 — see spec #28 task 1.8")

    # ------------------------------------------------------------------
    # Co-occurrences
    # ------------------------------------------------------------------

    def rebuild_cooccurrences(self, *, episode_ids: Optional[List[str]] = None) -> int:
        """Rebuild ``entity_cooccurrences`` for the given episodes.

        Spec §1.7: called automatically at the end of ``resolve-entities``
        for affected episodes, and via ``thestill rebuild-cooccurrences``
        for full rebuilds. Returns the number of rows materialised.
        """
        raise NotImplementedError("Phase 1 — see spec #28 task 1.7")
