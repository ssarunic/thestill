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

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from structlog import get_logger

from ..models.entities import EntityMention, EntityRecord, EntityType, MentionRole, ResolutionMethod, ResolutionStatus

logger = get_logger(__name__)

# Keeper-tiebreak ranking for ``find_duplicate_qid_pairs``. Derived
# from ``EntityType`` declaration order so adding/reordering a member
# can't silently desync the rule.
_TYPE_PRIORITY = {t.value: i for i, t in enumerate(EntityType)}


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
        single writer, ``busy_timeout=5000``). Also loads sqlite-vec
        when available so cascade DELETEs from ``episodes`` don't
        crash on the ``chunks_ad`` trigger.
        """
        from ..utils.sqlite_ext import maybe_load_vec_extension

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        maybe_load_vec_extension(conn)
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

        Single atomic ``INSERT ... ON CONFLICT(id) DO UPDATE`` rather
        than a SELECT-then-INSERT/UPDATE pair: under the entity-branch
        worker pool's parallelism (``RESOLVE_ENTITIES_PARALLEL_JOBS>1``
        plus the inline ``_merge_qid_duplicates_for`` step that deletes
        loser entities), the SELECT-gap could race with another
        worker's CASCADE delete and produce a phantom INSERT that
        FK-failed downstream. The atomic upsert closes that window.

        Aliases are merged with the existing column value via the
        ``json_each`` aggregate inside the ON CONFLICT clause —
        repeated calls with partial alias sets accumulate rather than
        overwrite, same contract as before.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        new_aliases_json = json.dumps(entity.aliases)
        new_p31_json = json.dumps(entity.wikidata_instance_of)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO entities (
                    id, type, canonical_name, wikidata_qid,
                    aliases, description, wikidata_instance_of,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    type           = excluded.type,
                    canonical_name = excluded.canonical_name,
                    wikidata_qid   = COALESCE(excluded.wikidata_qid, entities.wikidata_qid),
                    aliases        = (
                        SELECT json_group_array(value) FROM (
                            SELECT DISTINCT value FROM (
                                SELECT value FROM json_each(entities.aliases)
                                UNION
                                SELECT value FROM json_each(excluded.aliases)
                            ) ORDER BY value
                        )
                    ),
                    description    = COALESCE(excluded.description, entities.description),
                    wikidata_instance_of = CASE
                        WHEN json_array_length(excluded.wikidata_instance_of) > 0
                        THEN excluded.wikidata_instance_of
                        ELSE entities.wikidata_instance_of
                    END,
                    updated_at     = excluded.updated_at
                """,
                (
                    entity.id,
                    entity.type.value,
                    entity.canonical_name,
                    entity.wikidata_qid,
                    new_aliases_json,
                    entity.description,
                    new_p31_json,
                    entity.created_at.isoformat(),
                    now_iso,
                ),
            )
            # ``rowcount`` is 1 on either INSERT or DO UPDATE; the log
            # distinction is only useful on real INSERTs but we can't
            # cheaply tell them apart without another query, so log all
            # successful upserts under a single key.
            if cursor.rowcount == 1:
                logger.info(
                    "entity_upserted",
                    entity_id=entity.id,
                    qid=entity.wikidata_qid,
                )
        return entity.id

    def get_entity(self, entity_id: str) -> Optional[EntityRecord]:
        """Look up by canonical ``"{type}:{slug}"`` id."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?",
                (entity_id,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def find_entity_by_qid(self, wikidata_qid: str) -> Optional[EntityRecord]:
        """Look up by Wikidata QID (used during resolution merging)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE wikidata_qid = ? LIMIT 1",
                (wikidata_qid,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def list_entities_by_type(self, entity_type: str) -> List[EntityRecord]:
        """Return every entity of the given type (used by alias-merge)."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = ? ORDER BY canonical_name",
                (entity_type,),
            ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def delete_entity(self, entity_id: str) -> bool:
        """Hard-delete an entity. ``ON DELETE CASCADE`` removes mentions
        + cooccurrence rows pointing at it. Returns True if a row was
        deleted (for use by the alias-merge job, which collapses the
        loser of a duplicate pair after re-pointing its mentions).
        """
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            return cursor.rowcount > 0

    def repoint_mentions(self, *, from_entity_id: str, to_entity_id: str) -> int:
        """Bulk-UPDATE every mention pointing at ``from_entity_id`` to
        point at ``to_entity_id``. Returns rowcount. Used by alias-merge
        before deleting the loser of a duplicate pair so cascade
        doesn't take the mentions with it.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE entity_mentions SET entity_id = ? WHERE entity_id = ?",
                (to_entity_id, from_entity_id),
            )
            return cursor.rowcount

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
                m.surface_label,
                m.quote_excerpt,
                m.sentiment,
                m.confidence,
                m.extractor,
                m.resolution_method.value if m.resolution_method else None,
                json.dumps(m.candidate_entity_ids) if m.candidate_entity_ids else None,
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
                    surface_label, quote_excerpt, sentiment, confidence,
                    extractor, resolution_method, candidate_entity_ids,
                    created_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        Drives the ``resolve-entities`` batching loop. ``episode_id``
        scopes to one episode (handler-driven path); without it the
        full backlog is returned (CLI ``thestill resolve-entities`` /
        eventual rebuild path).
        """
        sql = "SELECT * FROM entity_mentions WHERE resolution_status = 'pending'"
        params: list = []
        if episode_id is not None:
            sql += " AND episode_id = ?"
            params.append(episode_id)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_mention(r) for r in rows]

    def resolve_mention(
        self,
        *,
        mention_id: int,
        entity_id: Optional[str],
        status: str,
        resolved_at: Optional[datetime] = None,
        method: Optional[str] = None,
        candidate_entity_ids: Optional[List[str]] = None,
    ) -> bool:
        """Flip a pending mention to a terminal status.

        Accepted ``status`` values: ``resolved`` | ``unresolvable`` |
        ``ambiguous`` | ``dropped``. ``entity_id`` is required for
        ``resolved`` and ignored (stored as NULL by the caller) for
        the other statuses. ``method`` is one of ``ResolutionMethod``;
        ``candidate_entity_ids`` is populated only when status is
        ``ambiguous`` (spec §1.13.5).
        """
        if status not in ("resolved", "unresolvable", "ambiguous", "dropped"):
            raise ValueError(f"invalid resolution status={status!r}")
        ts = (resolved_at or datetime.now(timezone.utc)).isoformat()
        candidates_json = json.dumps(candidate_entity_ids) if candidate_entity_ids else None
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE entity_mentions
                SET entity_id = ?,
                    resolution_status = ?,
                    resolved_at = ?,
                    resolution_method = COALESCE(?, resolution_method),
                    candidate_entity_ids = ?
                WHERE id = ?
                """,
                (entity_id, status, ts, method, candidates_json, mention_id),
            )
            return cursor.rowcount > 0

    def find_mentions(
        self,
        *,
        entity_id: Optional[str] = None,
        entity_type: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_id: Optional[str] = None,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        role: Optional[str] = None,
        limit: int = 50,
    ) -> List["MentionContext"]:
        """Spec #28 §1.8 — backing query for ``find_mentions`` MCP tool.

        Returns mentions joined with their owning episode + podcast so
        the MCP tool can hand back citation-shaped rows (Strategy §4)
        without a second round-trip per result. Only resolved mentions
        are returned — pending/unresolvable rows have no canonical
        ``entity_id`` to filter by anyway.

        ``entity_id`` filters to a specific entity (``"person:elon-musk"``);
        ``entity_type`` filters to a category (``"person"``). Both
        compose with podcast/episode/date_range/role.
        """
        sql = """
            SELECT m.*, e.title AS episode_title, e.pub_date AS episode_pub_date,
                   p.id AS podcast_id, p.title AS podcast_title, p.slug AS podcast_slug,
                   ent.type AS entity_type, ent.canonical_name AS entity_canonical_name
            FROM entity_mentions m
            JOIN episodes e ON m.episode_id = e.id
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN entities ent ON m.entity_id = ent.id
            WHERE m.resolution_status = 'resolved'
        """
        params: list = []
        if entity_id is not None:
            sql += " AND m.entity_id = ?"
            params.append(entity_id)
        if entity_type is not None:
            sql += " AND ent.type = ?"
            params.append(entity_type)
        if episode_id is not None:
            sql += " AND m.episode_id = ?"
            params.append(episode_id)
        if podcast_id is not None:
            sql += " AND p.id = ?"
            params.append(podcast_id)
        if date_range is not None:
            sql += " AND e.pub_date BETWEEN ? AND ?"
            params.append(date_range[0].isoformat())
            params.append(date_range[1].isoformat())
        if role is not None:
            sql += " AND m.role = ?"
            params.append(role)
        sql += " ORDER BY e.pub_date DESC, m.start_ms ASC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_mention_context(r) for r in rows]

    def list_mentions_by_speaker(
        self,
        *,
        speaker: str,
        topic_entity_id: Optional[str] = None,
        podcast_id: Optional[str] = None,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        limit: int = 50,
    ) -> List["MentionContext"]:
        """Spec #28 §1.8 — backing query for ``list_quotes_by`` MCP tool.

        Filters resolved mentions to those whose ``speaker`` field
        matches (case-insensitive substring — diarisation labels vary
        slightly across episodes). ``topic_entity_id`` constrains the
        intersect to the same diarisation segment, not just episode
        membership: a Galloway quote in a segment that doesn't mention
        SpaceX is filtered out even if the episode covers SpaceX in a
        different segment. This is the contract behind "what has X
        said about Y" — the topic must surface in the same speech
        window as the speaker's words.
        """
        sql = """
            SELECT m.*, e.title AS episode_title, e.pub_date AS episode_pub_date,
                   p.id AS podcast_id, p.title AS podcast_title, p.slug AS podcast_slug,
                   ent.type AS entity_type, ent.canonical_name AS entity_canonical_name
            FROM entity_mentions m
            JOIN episodes e ON m.episode_id = e.id
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN entities ent ON m.entity_id = ent.id
            WHERE m.resolution_status = 'resolved'
              AND m.speaker IS NOT NULL
              AND LOWER(m.speaker) LIKE LOWER(?)
        """
        params: list = [f"%{speaker}%"]
        if topic_entity_id is not None:
            sql += """
                AND EXISTS (
                    SELECT 1 FROM entity_mentions m2
                    WHERE m2.episode_id = m.episode_id
                      AND m2.segment_id = m.segment_id
                      AND m2.entity_id = ?
                      AND m2.resolution_status = 'resolved'
                )
            """
            params.append(topic_entity_id)
        if podcast_id is not None:
            sql += " AND p.id = ?"
            params.append(podcast_id)
        if date_range is not None:
            sql += " AND e.pub_date BETWEEN ? AND ?"
            params.append(date_range[0].isoformat())
            params.append(date_range[1].isoformat())
        sql += " ORDER BY e.pub_date DESC, m.start_ms ASC LIMIT ?"
        params.append(limit)
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_mention_context(r) for r in rows]

    def get_mention_for_clip(
        self,
        *,
        episode_id: str,
        start_ms: int,
        end_ms: Optional[int] = None,
    ) -> Optional["MentionContext"]:
        """Return the resolved mention closest to ``start_ms`` for the
        episode — backing query for ``get_episode_clip`` MCP tool.

        Strategy: pick the resolved mention whose start_ms straddles
        the requested ``start_ms`` (within the segment), or the
        nearest one if none straddles. ``end_ms`` is reported in the
        result via the segment bounds — callers can override via the
        ``±sec`` window in the MCP tool.
        """
        sql_base = """
            SELECT m.*, e.title AS episode_title, e.pub_date AS episode_pub_date,
                   p.id AS podcast_id, p.title AS podcast_title, p.slug AS podcast_slug,
                   ent.type AS entity_type, ent.canonical_name AS entity_canonical_name
            FROM entity_mentions m
            JOIN episodes e ON m.episode_id = e.id
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN entities ent ON m.entity_id = ent.id
            WHERE m.resolution_status = 'resolved'
              AND m.episode_id = ?
        """
        with self._get_connection() as conn:
            # Try a straddling match first; fall back to nearest by
            # absolute distance from the requested start_ms.
            row = conn.execute(
                sql_base + " AND m.start_ms <= ? AND m.end_ms >= ? ORDER BY m.start_ms LIMIT 1",
                (episode_id, start_ms, start_ms),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    sql_base + " ORDER BY ABS(m.start_ms - ?) LIMIT 1",
                    (episode_id, start_ms),
                ).fetchone()
        if row is None:
            return None
        return _row_to_mention_context(row)

    # ------------------------------------------------------------------
    # Entity-page assembly (spec §1.8 — get_entity)
    # ------------------------------------------------------------------

    def get_entity_summary(
        self,
        entity_id: str,
        *,
        cooccurring_limit: int = 20,
        recent_mentions_limit: int = 10,
    ) -> Optional[dict]:
        """Return entity + mention_count + cooccurring + recent_mentions.

        Mirrors the spec's ``get_entity`` MCP tool output shape. Returns
        ``None`` if the entity doesn't exist.
        """
        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        with self._get_connection() as conn:
            mention_count = conn.execute(
                "SELECT COUNT(*) FROM entity_mentions " "WHERE entity_id = ? AND resolution_status = 'resolved'",
                (entity_id,),
            ).fetchone()[0]
            # Co-occurring entities — fetch from canonical-pair table
            # and unify into a single column. Order by episode_count.
            cooccur_rows = conn.execute(
                """
                SELECT
                    CASE WHEN c.entity_a_id = ? THEN c.entity_b_id
                         ELSE c.entity_a_id END AS other_id,
                    c.episode_count,
                    c.last_seen_at
                FROM entity_cooccurrences c
                WHERE c.entity_a_id = ? OR c.entity_b_id = ?
                ORDER BY c.episode_count DESC
                LIMIT ?
                """,
                (entity_id, entity_id, entity_id, cooccurring_limit),
            ).fetchall()
        cooccurring = []
        for row in cooccur_rows:
            other = self.get_entity(row["other_id"])
            if other is None:
                continue
            cooccurring.append(
                {
                    "entity": other,
                    "episode_count": row["episode_count"],
                    "last_seen_at": row["last_seen_at"],
                }
            )
        recent_mentions = self.find_mentions(entity_id=entity_id, limit=recent_mentions_limit)
        roles = self.get_entity_roles(entity_id)
        return {
            "entity": entity,
            "mention_count": mention_count,
            "cooccurring": cooccurring,
            "recent_mentions": recent_mentions,
            "hosts_podcasts": roles["hosts_podcasts"],
            "recurring_podcasts": roles["recurring_podcasts"],
            "guest_episodes": roles["guest_episodes"],
        }

    def get_entity_roles(
        self,
        entity_id: str,
        *,
        guest_episodes_limit: int = 50,
    ) -> dict:
        """Return podcasts where this entity is a host/recurring and
        episodes where it appears as a guest.

        Spec #28 §1.13.1: host/guest is a property of the
        entity↔podcast (or entity↔episode) relationship, stored on
        ``podcasts.host_entity_ids`` / ``podcasts.recurring_entity_ids`` /
        ``episodes.guest_entity_ids``, *not* derived from
        ``entity_mentions``. Surface these so a host who never says
        their own name on the show still has their affiliation
        rendered on the entity page.
        """
        with self._get_connection() as conn:
            host_rows = conn.execute(
                """
                SELECT p.id           AS podcast_id,
                       p.slug         AS podcast_slug,
                       p.title        AS podcast_title,
                       (SELECT COUNT(*) FROM episodes WHERE podcast_id = p.id) AS episode_count
                FROM podcasts p
                WHERE EXISTS (
                    SELECT 1 FROM json_each(p.host_entity_ids) WHERE value = ?
                )
                ORDER BY p.title
                """,
                (entity_id,),
            ).fetchall()
            recurring_rows = conn.execute(
                """
                SELECT p.id           AS podcast_id,
                       p.slug         AS podcast_slug,
                       p.title        AS podcast_title,
                       (SELECT COUNT(*) FROM episodes WHERE podcast_id = p.id) AS episode_count
                FROM podcasts p
                WHERE EXISTS (
                    SELECT 1 FROM json_each(p.recurring_entity_ids) WHERE value = ?
                )
                ORDER BY p.title
                """,
                (entity_id,),
            ).fetchall()
            guest_rows = conn.execute(
                """
                SELECT e.id           AS episode_id,
                       e.slug         AS episode_slug,
                       e.title        AS episode_title,
                       e.pub_date     AS published_at,
                       p.id           AS podcast_id,
                       p.slug         AS podcast_slug,
                       p.title        AS podcast_title
                FROM episodes e
                JOIN podcasts p ON p.id = e.podcast_id
                WHERE EXISTS (
                    SELECT 1 FROM json_each(e.guest_entity_ids) WHERE value = ?
                )
                ORDER BY COALESCE(e.pub_date, '') DESC, e.title
                LIMIT ?
                """,
                (entity_id, guest_episodes_limit),
            ).fetchall()
        return {
            "hosts_podcasts": [dict(r) for r in host_rows],
            "recurring_podcasts": [dict(r) for r in recurring_rows],
            "guest_episodes": [dict(r) for r in guest_rows],
        }

    def find_entity_by_name(self, name: str, *, entity_type: Optional[str] = None) -> Optional[EntityRecord]:
        """Resolve a free-form name (canonical name OR alias OR id)
        to an entity. Used by MCP tools that accept ``id_or_name``.
        Case-insensitive on canonical_name; exact match on id; alias
        match is JSON LIKE.
        """
        with self._get_connection() as conn:
            # First try exact id match
            row = conn.execute(
                "SELECT * FROM entities WHERE id = ?",
                (name,),
            ).fetchone()
            if row is None:
                sql = "SELECT * FROM entities WHERE LOWER(canonical_name) = LOWER(?)"
                params: list = [name]
                if entity_type is not None:
                    sql += " AND type = ?"
                    params.append(entity_type)
                sql += " LIMIT 1"
                row = conn.execute(sql, params).fetchone()
            if row is None:
                # Alias match: aliases is a JSON array stored as TEXT.
                # The cheap LIKE-based scan is fine because ``entities``
                # is small (~1k-10k rows in v1).
                sql = "SELECT * FROM entities WHERE aliases LIKE ?"
                params = [f'%"{name}"%']
                if entity_type is not None:
                    sql += " AND type = ?"
                    params.append(entity_type)
                sql += " LIMIT 1"
                row = conn.execute(sql, params).fetchone()
        return _row_to_entity(row) if row else None

    def search_entities_by_prefix(
        self,
        prefix: str,
        *,
        types: Optional[Tuple[str, ...]] = None,
        limit_per_type: int = 5,
    ) -> List["EntityHit"]:
        """Spec #28 §4.1 — typeahead lookup for the ⌘K command bar.

        Case-insensitive substring match against ``canonical_name`` and
        the JSON-encoded ``aliases`` array, ranked by resolved-mention
        count (most-mentioned first). ``types`` restricts to one or
        more ``entity_type`` values; ``None`` returns hits across all
        four types. ``limit_per_type`` caps the rows per type so a busy
        ``person`` bucket can't crowd out the ``company`` / ``topic``
        buckets in the typeahead.

        Returns ``EntityHit`` rows (see module bottom) — id, type,
        canonical name, the alias that matched (if it was an alias hit
        rather than a name hit), and the resolved-mention count for
        ranking on the client side.
        """
        prefix = (prefix or "").strip()
        if not prefix:
            return []
        like_pattern = f"%{prefix.lower()}%"
        # JSON aliases are stored case-preserving — match the lower-cased
        # value against a lower-cased copy of the column.
        type_clause = ""
        type_params: list = []
        if types:
            placeholders = ",".join("?" for _ in types)
            type_clause = f"AND e.type IN ({placeholders})"
            type_params = list(types)
        # Roles boost: a guest on an episode (or a host of a podcast)
        # is conceptually the most important entity for that show — the
        # entire episode is *about* them — but they often don't say
        # their own name, so mention_count alone ranks them at zero.
        # We compute role_score = max(3 if guest, 2 if host, 1 if
        # recurring, 0 otherwise) and order by that *before*
        # mention_count, so anchor entities float to the top regardless
        # of how often the transcript names them.
        sql = f"""
            WITH role_index AS (
                SELECT json_each.value AS entity_id,
                       3 AS role_score, 'guest' AS role, episodes.id AS episode_id
                FROM episodes, json_each(episodes.guest_entity_ids)
                WHERE episodes.guest_entity_ids != '[]'
                UNION ALL
                SELECT json_each.value AS entity_id,
                       2 AS role_score, 'host' AS role, episodes.id AS episode_id
                FROM episodes
                JOIN podcasts ON podcasts.id = episodes.podcast_id,
                     json_each(podcasts.host_entity_ids)
                WHERE podcasts.host_entity_ids != '[]'
                UNION ALL
                SELECT json_each.value AS entity_id,
                       1 AS role_score, 'recurring' AS role, episodes.id AS episode_id
                FROM episodes
                JOIN podcasts ON podcasts.id = episodes.podcast_id,
                     json_each(podcasts.recurring_entity_ids)
                WHERE podcasts.recurring_entity_ids != '[]'
            ),
            role_agg AS (
                SELECT entity_id,
                       MAX(role_score) AS role_score,
                       COUNT(DISTINCT episode_id) AS role_episode_count
                FROM role_index
                GROUP BY entity_id
            ),
            mention_agg AS (
                SELECT entity_id, COUNT(*) AS mention_count
                FROM entity_mentions
                WHERE resolution_status = 'resolved'
                GROUP BY entity_id
            ),
            ranked AS (
                SELECT
                    e.id              AS id,
                    e.type            AS type,
                    e.canonical_name  AS canonical_name,
                    e.aliases         AS aliases,
                    COALESCE(ma.mention_count, 0) AS mention_count,
                    COALESCE(ra.role_score, 0)    AS role_score,
                    COALESCE(ra.role_episode_count, 0) AS role_episode_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.type
                        ORDER BY COALESCE(ra.role_score, 0)             DESC,
                                 COALESCE(ra.role_episode_count, 0)     DESC,
                                 COALESCE(ma.mention_count, 0)          DESC,
                                 LENGTH(e.canonical_name)               ASC,
                                 e.canonical_name                       ASC
                    ) AS rn
                FROM entities e
                LEFT JOIN role_agg    ra ON ra.entity_id = e.id
                LEFT JOIN mention_agg ma ON ma.entity_id = e.id
                WHERE (LOWER(e.canonical_name) LIKE ?
                       OR LOWER(e.aliases) LIKE ?)
                  {type_clause}
            )
            SELECT id, type, canonical_name, aliases, mention_count,
                   role_score, role_episode_count
            FROM ranked
            WHERE rn <= ?
            ORDER BY role_score DESC,
                     role_episode_count DESC,
                     mention_count DESC,
                     LENGTH(canonical_name) ASC,
                     canonical_name ASC
        """
        params = [like_pattern, like_pattern, *type_params, limit_per_type]
        with self._get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        hits: List[EntityHit] = []
        prefix_lower = prefix.lower()
        for row in rows:
            aliases = json.loads(row["aliases"] or "[]")
            matched_alias: Optional[str] = None
            if prefix_lower not in row["canonical_name"].lower():
                for alias in aliases:
                    if prefix_lower in alias.lower():
                        matched_alias = alias
                        break
            role_score = row["role_score"]
            role: Optional[str] = None
            if role_score == 3:
                role = "guest"
            elif role_score == 2:
                role = "host"
            elif role_score == 1:
                role = "recurring"
            hits.append(
                EntityHit(
                    id=row["id"],
                    type=row["type"],
                    canonical_name=row["canonical_name"],
                    matched_alias=matched_alias,
                    mention_count=row["mention_count"],
                    role=role,
                    role_episode_count=row["role_episode_count"],
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Co-occurrences
    # ------------------------------------------------------------------

    def rebuild_cooccurrences(self, *, episode_ids: Optional[List[str]] = None) -> int:
        """Rebuild ``entity_cooccurrences`` for the given episodes.

        Called automatically at the end of ``resolve-entities`` for
        affected episodes, and via ``thestill rebuild-cooccurrences``
        for full rebuilds.

        ``episode_count`` is "distinct episodes containing the pair
        across the whole corpus" — NOT a running counter. So even when
        scoped to specific episodes, the per-pair aggregate has to be
        recomputed corpus-wide for any pair touched by the scope. We
        achieve this by:
        1. Collecting the entity-set that appears in the scoped
           episodes (any pair touching one of these entities is
           potentially stale).
        2. Deleting all rows in ``entity_cooccurrences`` that touch any
           of those entities (DELETE WHERE a_id IN ... OR b_id IN ...).
        3. INSERT … SELECT the corpus-wide aggregate for pairs where at
           least one entity is in the affected set.
        4. Returns the number of cooccurrence rows materialised.

        ``episode_ids=None`` is a full rebuild — wipe-and-replace.
        """
        with self._get_connection() as conn:
            if episode_ids is None:
                conn.execute("DELETE FROM entity_cooccurrences")
                affected_predicate = ""
                params: list = []
            else:
                if not episode_ids:
                    return 0
                # Find the entity_ids touched by these episodes; only
                # those have potentially-stale cooccurrence rows.
                placeholders = ",".join("?" * len(episode_ids))
                affected_rows = conn.execute(
                    f"""
                    SELECT DISTINCT entity_id FROM entity_mentions
                    WHERE entity_id IS NOT NULL
                      AND resolution_status = 'resolved'
                      AND episode_id IN ({placeholders})
                    """,
                    list(episode_ids),
                ).fetchall()
                affected_ids = [r["entity_id"] for r in affected_rows]
                if not affected_ids:
                    return 0
                aff_placeholders = ",".join("?" * len(affected_ids))
                conn.execute(
                    f"""
                    DELETE FROM entity_cooccurrences
                    WHERE entity_a_id IN ({aff_placeholders})
                       OR entity_b_id IN ({aff_placeholders})
                    """,
                    affected_ids + affected_ids,
                )
                affected_predicate = (
                    f" AND (a.entity_id IN ({aff_placeholders}) " f"     OR b.entity_id IN ({aff_placeholders}))"
                )
                params = affected_ids + affected_ids

            # Self-join scoped to resolved mentions; canonical pair
            # ordering via the ``a.entity_id < b.entity_id`` predicate
            # in the JOIN clause matches the ``CHECK (a < b)`` on the
            # target table.
            cursor = conn.execute(
                f"""
                INSERT INTO entity_cooccurrences (
                    entity_a_id, entity_b_id, episode_count, last_seen_at
                )
                SELECT
                    a.entity_id,
                    b.entity_id,
                    COUNT(DISTINCT a.episode_id),
                    MAX(COALESCE(a.resolved_at, a.created_at))
                FROM entity_mentions a
                JOIN entity_mentions b
                    ON a.episode_id = b.episode_id
                   AND a.entity_id < b.entity_id
                WHERE a.resolution_status = 'resolved'
                  AND b.resolution_status = 'resolved'
                  {affected_predicate}
                GROUP BY a.entity_id, b.entity_id
                """,
                params,
            )
            inserted = cursor.rowcount
        logger.info(
            "cooccurrences_rebuilt",
            scope_episode_count=len(episode_ids) if episode_ids else None,
            rows=inserted,
        )
        return inserted

    # ------------------------------------------------------------------
    # Spec #28 §1.13.1 — host / guest / recurring anchor metadata
    # ------------------------------------------------------------------

    def set_podcast_hosts(self, podcast_id: str, entity_ids: List[str]) -> None:
        """Replace ``podcasts.host_entity_ids`` with the given list."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE podcasts SET host_entity_ids = ? WHERE id = ?",
                (json.dumps(list(entity_ids)), podcast_id),
            )

    def set_podcast_recurring(self, podcast_id: str, entity_ids: List[str]) -> None:
        """Replace ``podcasts.recurring_entity_ids`` with the given list."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE podcasts SET recurring_entity_ids = ? WHERE id = ?",
                (json.dumps(list(entity_ids)), podcast_id),
            )

    def set_episode_guests(self, episode_id: str, entity_ids: List[str]) -> None:
        """Replace ``episodes.guest_entity_ids`` with the given list."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE episodes SET guest_entity_ids = ? WHERE id = ?",
                (json.dumps(list(entity_ids)), episode_id),
            )

    def get_podcast_anchors(self, podcast_id: str) -> dict:
        """Return ``{'hosts': [...], 'recurring': [...]}`` for one podcast."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT host_entity_ids, recurring_entity_ids FROM podcasts WHERE id = ?",
                (podcast_id,),
            ).fetchone()
        if row is None:
            return {"hosts": [], "recurring": []}
        return {
            "hosts": json.loads(row["host_entity_ids"] or "[]"),
            "recurring": json.loads(row["recurring_entity_ids"] or "[]"),
        }

    def get_episode_anchors(self, episode_id: str) -> List[str]:
        """Return the union of host + recurring + guest entity ids for an
        episode. Used by the extractor's anchor-injection pass.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT p.host_entity_ids, p.recurring_entity_ids,
                       e.guest_entity_ids
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id = ?
                """,
                (episode_id,),
            ).fetchone()
        if row is None:
            return []
        ids: List[str] = []
        for column in ("host_entity_ids", "recurring_entity_ids", "guest_entity_ids"):
            ids.extend(json.loads(row[column] or "[]"))
        # Preserve order, drop duplicates
        seen: set = set()
        unique: List[str] = []
        for entity_id in ids:
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                unique.append(entity_id)
        return unique

    def detect_top_speakers(self, podcast_id: str, *, limit: int = 5) -> List[Tuple[str, int]]:
        """Spec §1.13.1 — propose hosts by speaker frequency.

        Returns ``(speaker_label, segment_count)`` rows ordered by
        frequency descending. The CLI consumes this list and asks the
        operator to pick which speakers are hosts vs guests vs noise.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT m.speaker, COUNT(*) AS n
                FROM entity_mentions m
                JOIN episodes e ON m.episode_id = e.id
                WHERE e.podcast_id = ?
                  AND m.speaker IS NOT NULL
                  AND TRIM(m.speaker) != ''
                  AND LOWER(m.speaker) != 'unknown'
                GROUP BY m.speaker
                ORDER BY n DESC
                LIMIT ?
                """,
                (podcast_id, limit),
            ).fetchall()
        return [(r["speaker"], r["n"]) for r in rows]

    # ------------------------------------------------------------------
    # Spec #28 §1.13.5 — within-episode coreference helpers
    # ------------------------------------------------------------------

    def list_unresolved_person_mentions(self, episode_id: str) -> List[EntityMention]:
        """Mentions for one episode with surface_label='person' and
        ``resolution_status='unresolvable'``. Drives the coref pass.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM entity_mentions
                WHERE episode_id = ?
                  AND resolution_status = 'unresolvable'
                  AND (surface_label = 'person' OR surface_label IS NULL)
                """,
                (episode_id,),
            ).fetchall()
        return [_row_to_mention(r) for r in rows]

    def list_resolved_persons_for_episode(self, episode_id: str) -> List[EntityRecord]:
        """Distinct ``person``-typed entities resolved in this episode."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT e.*
                FROM entity_mentions m
                JOIN entities e ON m.entity_id = e.id
                WHERE m.episode_id = ?
                  AND m.resolution_status = 'resolved'
                  AND e.type = 'person'
                """,
                (episode_id,),
            ).fetchall()
        return [_row_to_entity(r) for r in rows]

    # ------------------------------------------------------------------
    # Spec #28 §1.13.7 — mention overrides + resolution blacklist
    # ------------------------------------------------------------------

    def add_override(
        self,
        *,
        surface_form: str,
        episode_id: Optional[str],
        kind: str,
        entity_id: Optional[str] = None,
        reason: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> int:
        """Insert a row into ``mention_overrides``. Returns the row id."""
        if kind not in ("drop", "force_entity", "force_unresolvable"):
            raise ValueError(f"invalid override kind={kind!r}")
        if kind == "force_entity" and not entity_id:
            raise ValueError("force_entity requires entity_id")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO mention_overrides
                    (surface_form, episode_id, override_kind, entity_id, reason, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (surface_form, episode_id, kind, entity_id, reason, created_by),
            )
            return int(cursor.lastrowid or 0)

    def lookup_override(self, surface_form: str, episode_id: Optional[str]) -> Optional[dict]:
        """Find an override matching ``(surface_form, episode_id)`` or
        ``(surface_form, NULL)`` (global). Episode-scoped wins over
        global. Returns the row as a dict or ``None``.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT id, surface_form, episode_id, override_kind, entity_id,
                       reason, created_by, created_at
                FROM mention_overrides
                WHERE LOWER(surface_form) = LOWER(?)
                  AND (episode_id = ? OR episode_id IS NULL)
                ORDER BY (episode_id IS NULL) ASC, id DESC
                LIMIT 1
                """,
                (surface_form, episode_id),
            ).fetchone()
        return dict(row) if row else None

    def list_overrides(self, *, limit: int = 200) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM mention_overrides ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_mention(self, mention_id: int) -> Optional[EntityMention]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM entity_mentions WHERE id = ?",
                (mention_id,),
            ).fetchone()
        return _row_to_mention(row) if row else None

    def add_blacklist_entry(
        self,
        *,
        surface_form: str,
        wrong_qid: str,
        reason: Optional[str] = None,
    ) -> int:
        """Negative cache: refuse to ground ``surface_form → wrong_qid``."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO resolution_blacklist
                    (surface_form, wrong_qid, reason)
                VALUES (?, ?, ?)
                """,
                (surface_form, wrong_qid, reason),
            )
            return int(cursor.lastrowid or 0)

    def is_blacklisted(self, surface_form: str, wrong_qid: str) -> bool:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM resolution_blacklist
                WHERE LOWER(surface_form) = LOWER(?) AND wrong_qid = ?
                LIMIT 1
                """,
                (surface_form, wrong_qid),
            ).fetchone()
        return row is not None

    def list_blacklist(self, *, limit: int = 200) -> List[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM resolution_blacklist ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Alias merging (spec §1.6)
    # ------------------------------------------------------------------

    def find_duplicate_qid_pairs(self) -> List[Tuple[str, str, str]]:
        """Find pairs of entities sharing a Wikidata QID.

        Keeper rank: ``mention_count DESC, type_priority ASC, id ASC``.
        Mention count is the strongest signal — for an entity with
        hundreds of mentions, GLiNER's majority surface label tells us
        what type the entity actually is. Type priority
        (``person < company < product < topic``) only breaks ties when
        counts are equal; ``id`` is the deterministic stabiliser.

        Why this matters: an earlier ``MIN(id)`` rule tipped duplicates
        toward whichever type prefix sorted first alphabetically — for
        Q22686 (Donald Trump) that meant ``company:donald-trump`` beat
        ``person:donald-trump`` even though 500+ mentions were labeled
        ``person`` and a handful (e.g. "Trump administration") were
        labeled ``company``.

        Returns list of ``(qid, keeper_id, loser_id)`` tuples; the
        loser's mentions get repointed at the keeper and the loser is
        deleted by the caller.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    e.wikidata_qid,
                    e.id,
                    e.type,
                    COALESCE(
                        (SELECT COUNT(*) FROM entity_mentions m WHERE m.entity_id = e.id),
                        0
                    ) AS mention_count
                FROM entities e
                WHERE e.wikidata_qid IS NOT NULL
                  AND e.wikidata_qid IN (
                      SELECT wikidata_qid FROM entities
                      WHERE wikidata_qid IS NOT NULL
                      GROUP BY wikidata_qid
                      HAVING COUNT(*) > 1
                  )
                """
            ).fetchall()

        by_qid: Dict[str, List[sqlite3.Row]] = {}
        for row in rows:
            by_qid.setdefault(row["wikidata_qid"], []).append(row)

        pairs: List[Tuple[str, str, str]] = []
        for qid, entries in by_qid.items():
            entries.sort(
                key=lambda r: (
                    -r["mention_count"],
                    _TYPE_PRIORITY.get(r["type"], len(_TYPE_PRIORITY)),
                    r["id"],
                )
            )
            keeper = entries[0]["id"]
            for entry in entries[1:]:
                pairs.append((qid, keeper, entry["id"]))
        return pairs

    def find_mistyped_entities(
        self,
        *,
        min_mentions: int = 3,
        min_majority_ratio: float = 0.6,
    ) -> List[Tuple[str, str, str, int, int]]:
        """Spec #28 §1.6 follow-up — return entities whose stored type
        disagrees with the majority surface_label of their mentions.

        Output: list of ``(entity_id, current_type, suggested_type,
        majority_count, total_count)`` tuples.

        Filters out entities where:

        - total resolved mentions < ``min_mentions`` (not enough signal
          to override the model's typing — default 3 is conservative).
        - the majority surface_label fails to clear ``min_majority_ratio``
          of the total (default 0.6 — when GLiNER itself is split, we
          shouldn't bulldoze the existing type).
        - the majority surface_label is not one of the four mappable
          types (e.g. legacy ``MISC`` rows).
        """
        valid_types = {t.value for t in EntityType}
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    e.id AS entity_id,
                    e.type AS current_type,
                    m.surface_label AS surface_label,
                    COUNT(*) AS label_count
                FROM entities e
                JOIN entity_mentions m ON m.entity_id = e.id
                WHERE m.surface_label IS NOT NULL
                GROUP BY e.id, m.surface_label
                """
            ).fetchall()

        per_entity: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            per_entity.setdefault(row["entity_id"], {"current": row["current_type"], "labels": {}})
            per_entity[row["entity_id"]]["labels"][row["surface_label"]] = row["label_count"]

        out: List[Tuple[str, str, str, int, int]] = []
        for entity_id, info in per_entity.items():
            total = sum(info["labels"].values())
            if total < min_mentions:
                continue
            top_label, top_count = max(info["labels"].items(), key=lambda kv: (kv[1], kv[0]))
            if top_label not in valid_types:
                continue
            if top_label == info["current"]:
                continue
            if top_count / total < min_majority_ratio:
                continue
            out.append((entity_id, info["current"], top_label, top_count, total))
        out.sort(key=lambda r: (-r[3], r[0]))
        return out


def _row_to_entity(row: sqlite3.Row) -> EntityRecord:
    # ``wikidata_instance_of`` was added by the spec #28 §5.2 migration;
    # legacy rows pre-migration may have NULL even though the column has
    # a DEFAULT '[]'. Guard with ``or '[]'`` to keep the JSON load happy.
    p31_raw = _row_get(row, "wikidata_instance_of") or "[]"
    return EntityRecord(
        id=row["id"],
        type=EntityType(row["type"]),
        canonical_name=row["canonical_name"],
        wikidata_qid=row["wikidata_qid"],
        aliases=json.loads(row["aliases"] or "[]"),
        description=row["description"],
        wikidata_instance_of=json.loads(p31_raw),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_get(row: sqlite3.Row, key: str):
    """Best-effort column read for sqlite3.Row.

    ``sqlite3.Row.__getitem__`` raises ``IndexError`` for missing columns
    rather than returning ``None``, which is awkward when reading rows
    coming through legacy queries (e.g. tests that build a Row without
    every column). Falls back to ``None`` so the caller can default.
    """
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


@dataclass(frozen=True)
class EntityHit:
    """Spec #28 §4.1 — one row from ``search_entities_by_prefix``.

    Lightweight projection used by the ⌘K typeahead path: the full
    ``EntityRecord`` carries description + timestamps that aren't
    needed in a dropdown list. ``matched_alias`` is non-``None`` only
    when the prefix didn't hit ``canonical_name`` directly — the UI
    uses it to render "Musk → Elon Musk" hints.
    """

    id: str
    type: str
    canonical_name: str
    matched_alias: Optional[str]
    mention_count: int
    # Role boost — non-``None`` when this entity is anchored as a host
    # of a podcast or guest/recurring on episodes. Lets the typeahead
    # surface anchor entities even when they have zero transcript
    # mentions (a host who never says their own name).
    role: Optional[str] = None
    role_episode_count: int = 0


@dataclass(frozen=True)
class MentionContext:
    """A resolved ``EntityMention`` joined with its episode + podcast +
    entity, ready to render as a ``CitationRow`` (Strategy §4).

    The joined fields live alongside the mention rather than nested so
    the MCP-tool layer can pluck what it needs without re-joining.
    """

    mention: EntityMention
    episode_id: str
    episode_title: str
    episode_pub_date: Optional[datetime]
    podcast_id: str
    podcast_title: str
    podcast_slug: str
    entity_type: Optional[str]
    entity_canonical_name: Optional[str]


def _row_to_mention_context(row: sqlite3.Row) -> MentionContext:
    return MentionContext(
        mention=_row_to_mention(row),
        episode_id=row["episode_id"],
        episode_title=row["episode_title"],
        episode_pub_date=(datetime.fromisoformat(row["episode_pub_date"]) if row["episode_pub_date"] else None),
        podcast_id=row["podcast_id"],
        podcast_title=row["podcast_title"],
        podcast_slug=row["podcast_slug"],
        entity_type=row["entity_type"],
        entity_canonical_name=row["entity_canonical_name"],
    )


def _row_to_mention(row: sqlite3.Row) -> EntityMention:
    keys = set(row.keys())
    method_str = row["resolution_method"] if "resolution_method" in keys else None
    candidates_json = row["candidate_entity_ids"] if "candidate_entity_ids" in keys else None
    return EntityMention(
        id=row["id"],
        entity_id=row["entity_id"],
        resolution_status=ResolutionStatus(row["resolution_status"]),
        episode_id=row["episode_id"],
        segment_id=row["segment_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        speaker=row["speaker"],
        role=MentionRole(row["role"]) if row["role"] else None,
        surface_form=row["surface_form"],
        surface_label=row["surface_label"] if "surface_label" in keys else None,
        quote_excerpt=row["quote_excerpt"],
        sentiment=row["sentiment"],
        confidence=row["confidence"],
        extractor=row["extractor"],
        resolution_method=ResolutionMethod(method_str) if method_str else None,
        candidate_entity_ids=json.loads(candidates_json) if candidates_json else [],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )
