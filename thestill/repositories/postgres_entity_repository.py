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

"""PostgreSQL implementation of the entity repository (spec #44 Phase 2).

Faithful method-by-method port of ``SqliteEntityRepository`` behind the
shared ``EntityRepository`` ABC, applying the spec #44 dialect checklist:

- ``?`` placeholders → ``%s``; ``IN (?,?,...)`` expansion → ``= ANY(%s)``.
- ``entities.id`` stays ``text`` (slug ids like ``"person:elon-musk"``);
  ``episode_id`` / ``podcast_id`` are native ``uuid`` — pass str params,
  wrap reads with ``as_str``.
- Text-ISO timestamps → ``timestamptz``: pass tz-aware ``datetime``
  objects, read tz-aware datetimes back — no ``isoformat()`` /
  ``fromisoformat()`` anywhere.
- JSON-in-TEXT → ``jsonb``: writes wrapped in ``psycopg.types.json.Jsonb``,
  reads come back as ``list`` / ``dict`` directly (no ``json.loads``).
  ``json_each`` scans → ``jsonb_array_elements_text`` / ``@>`` containment.
- ``cursor.lastrowid`` → ``INSERT ... RETURNING id``.
- ``INSERT OR IGNORE`` → ``ON CONFLICT ... DO NOTHING``.
- SQLite's ASCII-case-insensitive ``LIKE`` on user-facing searches → ``ILIKE``.

The one deliberate contract quirk kept for fidelity:
``fetch_resolution_review_rows`` returns ``wikidata_instance_of`` as a JSON
*string* (its consumer, ``core.entity_review``, calls ``json.loads`` on it).
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg.types.json import Jsonb
from structlog import get_logger

from ..models.enrichment import EnrichmentStatus, EntityAffiliation, EntityEnrichment, EntityFact
from ..models.entities import EntityMention, EntityRecord, EntityType, MentionRole, ResolutionMethod, ResolutionStatus
from ..utils.postgres_ext import as_str, connect
from .entity_repository import EntityHit, EntityRepository, MentionContext

logger = get_logger(__name__)

# Keeper-tiebreak ranking for ``find_duplicate_qid_pairs`` — same rule as
# the SQLite implementation, derived from ``EntityType`` declaration order.
_TYPE_PRIORITY = {t.value: i for i, t in enumerate(EntityType)}

_MENTION_CONTEXT_SELECT = """
    SELECT m.*, e.title AS episode_title, e.pub_date AS episode_pub_date,
           p.id AS podcast_id, p.title AS podcast_title, p.slug AS podcast_slug,
           ent.type AS entity_type, ent.canonical_name AS entity_canonical_name
    FROM entity_mentions m
    JOIN episodes e ON m.episode_id = e.id
    JOIN podcasts p ON e.podcast_id = p.id
    LEFT JOIN entities ent ON m.entity_id = ent.id
    WHERE m.resolution_status = 'resolved'
"""


class PostgresEntityRepository(EntityRepository):
    """PostgreSQL-backed entity repository. Thread-safe via
    connection-per-operation; the schema is owned by
    ``postgres_schema.ensure_schema`` — this class writes no DDL.
    """

    def __init__(self, dsn: str, *, ensure_schema: bool = False):
        """
        Args:
            dsn: psycopg connection string.
            ensure_schema: bootstrap the full typed schema before use.
                The factory does this once per process; tests may opt in
                per-fixture.
        """
        self.dsn = dsn
        if ensure_schema:
            from .postgres_schema import ensure_schema as _ensure

            _ensure(dsn)
        logger.debug("PostgresEntityRepository initialized")

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def upsert_entity(self, entity: EntityRecord) -> str:
        """Create or update an ``entities`` row, returning its id.

        Single atomic ``INSERT ... ON CONFLICT (id) DO UPDATE`` — same
        race-closing contract as the SQLite version. Aliases are merged
        with the stored jsonb array (distinct union, sorted) so repeated
        calls with partial alias sets accumulate rather than overwrite.
        """
        now_dt = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                INSERT INTO entities (
                    id, type, canonical_name, wikidata_qid,
                    aliases, description, wikidata_instance_of,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    type           = EXCLUDED.type,
                    canonical_name = EXCLUDED.canonical_name,
                    wikidata_qid   = COALESCE(EXCLUDED.wikidata_qid, entities.wikidata_qid),
                    aliases        = (
                        SELECT COALESCE(jsonb_agg(value ORDER BY value), '[]'::jsonb)
                        FROM (
                            SELECT jsonb_array_elements_text(entities.aliases) AS value
                            UNION
                            SELECT jsonb_array_elements_text(EXCLUDED.aliases) AS value
                        ) merged
                    ),
                    description    = COALESCE(EXCLUDED.description, entities.description),
                    wikidata_instance_of = CASE
                        WHEN jsonb_array_length(EXCLUDED.wikidata_instance_of) > 0
                        THEN EXCLUDED.wikidata_instance_of
                        ELSE entities.wikidata_instance_of
                    END,
                    updated_at     = EXCLUDED.updated_at
                """,
                (
                    entity.id,
                    entity.type.value,
                    entity.canonical_name,
                    entity.wikidata_qid,
                    Jsonb(entity.aliases),
                    entity.description,
                    Jsonb(entity.wikidata_instance_of),
                    entity.created_at,
                    now_dt,
                ),
            )
            if cursor.rowcount == 1:
                logger.info(
                    "entity_upserted",
                    entity_id=entity.id,
                    qid=entity.wikidata_qid,
                )
        return entity.id

    def get_entity(self, entity_id: str) -> Optional[EntityRecord]:
        """Look up by canonical ``"{type}:{slug}"`` id."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = %s",
                (entity_id,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def find_entity_by_qid(self, wikidata_qid: str) -> Optional[EntityRecord]:
        """Look up by Wikidata QID (used during resolution merging)."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE wikidata_qid = %s LIMIT 1",
                (wikidata_qid,),
            ).fetchone()
        return _row_to_entity(row) if row else None

    def list_entities_by_type(self, entity_type: str) -> List[EntityRecord]:
        """Return every entity of the given type (used by alias-merge)."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = %s ORDER BY canonical_name",
                (entity_type,),
            ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def delete_entity(self, entity_id: str) -> bool:
        """Hard-delete an entity; ``ON DELETE CASCADE`` removes mentions
        + cooccurrence rows pointing at it.
        """
        with connect(self.dsn) as conn:
            cursor = conn.execute("DELETE FROM entities WHERE id = %s", (entity_id,))
            return cursor.rowcount > 0

    def repoint_mentions(self, *, from_entity_id: str, to_entity_id: str) -> int:
        """Bulk-UPDATE every mention pointing at ``from_entity_id`` to
        point at ``to_entity_id``. Returns rowcount.
        """
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "UPDATE entity_mentions SET entity_id = %s WHERE entity_id = %s",
                (to_entity_id, from_entity_id),
            )
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Mentions
    # ------------------------------------------------------------------

    def insert_mentions(self, mentions: Iterable[EntityMention]) -> int:
        """Bulk-insert ``entity_mentions`` rows; return rowcount.

        The identity ``id`` column is assigned by Postgres — input
        ``EntityMention.id`` (if any) is ignored. Empty input is a
        no-op that returns 0.
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
                Jsonb(m.candidate_entity_ids) if m.candidate_entity_ids else None,
                m.created_at,
                m.resolved_at,
            )
            for m in mentions
        ]
        if not rows:
            return 0
        with connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO entity_mentions (
                        entity_id, resolution_status, episode_id, segment_id,
                        start_ms, end_ms, speaker, role, surface_form,
                        surface_label, quote_excerpt, sentiment, confidence,
                        extractor, resolution_method, candidate_entity_ids,
                        created_at, resolved_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
        logger.info("entity_mentions_inserted", count=len(rows))
        return len(rows)

    def delete_mentions_for_episode(self, episode_id: str) -> int:
        """Wipe all ``entity_mentions`` for one episode; return rowcount."""
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "DELETE FROM entity_mentions WHERE episode_id = %s",
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
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM entity_mentions WHERE episode_id = %s",
                (episode_id,),
            ).fetchone()
        return row["n"] if row else 0

    def list_pending_mentions(
        self,
        *,
        episode_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[EntityMention]:
        """Return mentions with ``resolution_status='pending'``."""
        sql = "SELECT * FROM entity_mentions WHERE resolution_status = 'pending'"
        params: list = []
        if episode_id is not None:
            sql += " AND episode_id = %s"
            params.append(episode_id)
        sql += " ORDER BY id LIMIT %s"
        params.append(limit)
        with connect(self.dsn) as conn:
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
        """Flip a pending mention to a terminal status (see ABC)."""
        if status not in ("resolved", "unresolvable", "ambiguous", "dropped"):
            raise ValueError(f"invalid resolution status={status!r}")
        ts = resolved_at or datetime.now(timezone.utc)
        candidates = Jsonb(candidate_entity_ids) if candidate_entity_ids else None
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE entity_mentions
                SET entity_id = %s,
                    resolution_status = %s,
                    resolved_at = %s,
                    resolution_method = COALESCE(%s, resolution_method),
                    candidate_entity_ids = %s
                WHERE id = %s
                """,
                (entity_id, status, ts, method, candidates, mention_id),
            )
            return cursor.rowcount > 0

    def find_mention_ids_by_surface(
        self,
        surface_form: str,
        *,
        episode_id: Optional[str] = None,
        statuses: Tuple[str, ...] = ("resolved", "unresolvable", "ambiguous"),
    ) -> List[Tuple[int, str]]:
        """Return ``(mention_id, episode_id)`` for mentions of
        ``surface_form`` (case-insensitive) in the given statuses.
        """
        if not statuses:
            return []
        sql = (
            "SELECT id, episode_id FROM entity_mentions "
            "WHERE LOWER(surface_form) = LOWER(%s) AND resolution_status = ANY(%s)"
        )
        params: list = [surface_form, list(statuses)]
        if episode_id is not None:
            sql += " AND episode_id = %s"
            params.append(episode_id)
        with connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(int(r["id"]), as_str(r["episode_id"])) for r in rows]

    def reset_mentions_to_pending(self, mention_ids: List[int]) -> int:
        """Flip the given mentions back to ``pending`` for re-resolution."""
        if not mention_ids:
            return 0
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                """
                UPDATE entity_mentions
                SET resolution_status = 'pending',
                    entity_id = NULL,
                    resolved_at = NULL,
                    resolution_method = NULL,
                    candidate_entity_ids = NULL
                WHERE id = ANY(%s)
                """,
                (list(mention_ids),),
            )
            count = cursor.rowcount
        if count:
            logger.info("entity_mentions_reset_to_pending", count=count)
        return count

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
        """Spec #28 §1.8 — backing query for ``find_mentions`` MCP tool."""
        sql = _MENTION_CONTEXT_SELECT
        params: list = []
        if entity_id is not None:
            sql += " AND m.entity_id = %s"
            params.append(entity_id)
        if entity_type is not None:
            sql += " AND ent.type = %s"
            params.append(entity_type)
        if episode_id is not None:
            sql += " AND m.episode_id = %s"
            params.append(episode_id)
        if podcast_id is not None:
            sql += " AND p.id = %s"
            params.append(podcast_id)
        if date_range is not None:
            sql += " AND e.pub_date BETWEEN %s AND %s"
            params.append(date_range[0])
            params.append(date_range[1])
        if role is not None:
            sql += " AND m.role = %s"
            params.append(role)
        sql += " ORDER BY e.pub_date DESC NULLS LAST, m.start_ms ASC LIMIT %s"
        params.append(limit)
        with connect(self.dsn) as conn:
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

        ``ILIKE`` replaces SQLite's ``LOWER() LIKE LOWER()`` (user-facing
        search, per the port conventions).
        """
        sql = _MENTION_CONTEXT_SELECT + " AND m.speaker IS NOT NULL AND m.speaker ILIKE %s"
        params: list = [f"%{speaker}%"]
        if topic_entity_id is not None:
            sql += """
                AND EXISTS (
                    SELECT 1 FROM entity_mentions m2
                    WHERE m2.episode_id = m.episode_id
                      AND m2.segment_id = m.segment_id
                      AND m2.entity_id = %s
                      AND m2.resolution_status = 'resolved'
                )
            """
            params.append(topic_entity_id)
        if podcast_id is not None:
            sql += " AND p.id = %s"
            params.append(podcast_id)
        if date_range is not None:
            sql += " AND e.pub_date BETWEEN %s AND %s"
            params.append(date_range[0])
            params.append(date_range[1])
        sql += " ORDER BY e.pub_date DESC NULLS LAST, m.start_ms ASC LIMIT %s"
        params.append(limit)
        with connect(self.dsn) as conn:
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
        episode — straddling match first, nearest-by-distance fallback.
        """
        sql_base = _MENTION_CONTEXT_SELECT + " AND m.episode_id = %s"
        with connect(self.dsn) as conn:
            row = conn.execute(
                sql_base + " AND m.start_ms <= %s AND m.end_ms >= %s ORDER BY m.start_ms LIMIT 1",
                (episode_id, start_ms, start_ms),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    sql_base + " ORDER BY ABS(m.start_ms - %s) LIMIT 1",
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
        most_discussed_limit: int = 10,
    ) -> Optional[dict]:
        """Return entity + mention_count + cooccurring + recent_mentions
        (+ roles, most_discussed_on, enrichment — spec #45).
        """
        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        with connect(self.dsn) as conn:
            mention_count = conn.execute(
                "SELECT COUNT(*) AS n FROM entity_mentions WHERE entity_id = %s AND resolution_status = 'resolved'",
                (entity_id,),
            ).fetchone()["n"]
            cooccur_rows = conn.execute(
                """
                SELECT
                    CASE WHEN c.entity_a_id = %s THEN c.entity_b_id
                         ELSE c.entity_a_id END AS other_id,
                    c.episode_count,
                    c.last_seen_at
                FROM entity_cooccurrences c
                WHERE c.entity_a_id = %s OR c.entity_b_id = %s
                ORDER BY c.episode_count DESC
                LIMIT %s
                """,
                (entity_id, entity_id, entity_id, cooccurring_limit),
            ).fetchall()
            # ``GROUP BY p.id`` is legal here: p.id is the primary key, so
            # p.slug / p.title are functionally dependent (PG ≥ 9.1).
            most_discussed_rows = conn.execute(
                """
                SELECT p.id      AS podcast_id,
                       p.slug    AS podcast_slug,
                       p.title   AS podcast_title,
                       COUNT(*)  AS mention_count
                FROM entity_mentions m
                JOIN episodes e ON e.id = m.episode_id
                JOIN podcasts p ON p.id = e.podcast_id
                WHERE m.entity_id = %s AND m.resolution_status = 'resolved'
                GROUP BY p.id
                ORDER BY mention_count DESC, p.title
                LIMIT %s
                """,
                (entity_id, most_discussed_limit),
            ).fetchall()
            enrichment_row = conn.execute(
                "SELECT * FROM entity_enrichment WHERE entity_id = %s",
                (entity_id,),
            ).fetchone()
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
            "most_discussed_on": [{**r, "podcast_id": as_str(r["podcast_id"])} for r in most_discussed_rows],
            "enrichment": _row_to_enrichment(enrichment_row) if enrichment_row else None,
        }

    def get_entity_roles(
        self,
        entity_id: str,
        *,
        guest_episodes_limit: int = 50,
    ) -> dict:
        """Return podcasts where this entity is a host/recurring and
        episodes where it appears as a guest (spec #28 §1.13.1).

        The SQLite ``EXISTS (... json_each ...)`` scans become jsonb
        containment (``@>``) against the anchor id arrays.
        """
        with connect(self.dsn) as conn:
            host_rows = conn.execute(
                """
                SELECT p.id           AS podcast_id,
                       p.slug         AS podcast_slug,
                       p.title        AS podcast_title,
                       (SELECT COUNT(*) FROM episodes WHERE podcast_id = p.id) AS episode_count
                FROM podcasts p
                WHERE p.host_entity_ids @> to_jsonb(%s::text)
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
                WHERE p.recurring_entity_ids @> to_jsonb(%s::text)
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
                WHERE e.guest_entity_ids @> to_jsonb(%s::text)
                ORDER BY e.pub_date DESC NULLS LAST, e.title
                LIMIT %s
                """,
                (entity_id, guest_episodes_limit),
            ).fetchall()
        return {
            "hosts_podcasts": [{**r, "podcast_id": as_str(r["podcast_id"])} for r in host_rows],
            "recurring_podcasts": [{**r, "podcast_id": as_str(r["podcast_id"])} for r in recurring_rows],
            "guest_episodes": [
                {**r, "episode_id": as_str(r["episode_id"]), "podcast_id": as_str(r["podcast_id"])}
                for r in guest_rows
            ],
        }

    # ------------------------------------------------------------------
    # Enrichment (spec #45 Tier 0)
    # ------------------------------------------------------------------

    def upsert_enrichment(self, enrichment: EntityEnrichment) -> None:
        """Persist (insert or update) a Tier-0 enrichment row.

        ``created_at`` is preserved on update; content from a source
        that FAILED this run is preserved rather than wiped (spec #42
        FM-1) — same CASE ladder as the SQLite implementation.
        """
        facts = Jsonb([f.model_dump() for f in enrichment.facts])
        affiliations = Jsonb([a.model_dump() for a in enrichment.affiliations])
        now_dt = datetime.now(timezone.utc)
        with connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO entity_enrichment (
                    entity_id, image_url, image_attribution, image_license,
                    headline, wikipedia_extract, wikipedia_url,
                    facts_json, affiliations_json,
                    wikidata_status, wikidata_fetched_at,
                    wikipedia_status, wikipedia_fetched_at,
                    retry_after, schema_version, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (entity_id) DO UPDATE SET
                    image_url            = CASE WHEN EXCLUDED.image_url IS NULL
                                                  AND (EXCLUDED.wikidata_status = 'failed'
                                                       OR EXCLUDED.wikipedia_status = 'failed')
                                                THEN entity_enrichment.image_url
                                                ELSE EXCLUDED.image_url END,
                    image_attribution    = CASE WHEN EXCLUDED.image_url IS NULL
                                                  AND (EXCLUDED.wikidata_status = 'failed'
                                                       OR EXCLUDED.wikipedia_status = 'failed')
                                                THEN entity_enrichment.image_attribution
                                                ELSE EXCLUDED.image_attribution END,
                    image_license        = CASE WHEN EXCLUDED.image_url IS NULL
                                                  AND (EXCLUDED.wikidata_status = 'failed'
                                                       OR EXCLUDED.wikipedia_status = 'failed')
                                                THEN entity_enrichment.image_license
                                                ELSE EXCLUDED.image_license END,
                    headline             = CASE WHEN EXCLUDED.wikidata_status = 'failed'
                                                THEN entity_enrichment.headline
                                                ELSE EXCLUDED.headline END,
                    facts_json           = CASE WHEN EXCLUDED.wikidata_status = 'failed'
                                                THEN entity_enrichment.facts_json
                                                ELSE EXCLUDED.facts_json END,
                    affiliations_json    = CASE WHEN EXCLUDED.wikidata_status = 'failed'
                                                THEN entity_enrichment.affiliations_json
                                                ELSE EXCLUDED.affiliations_json END,
                    wikipedia_extract    = CASE WHEN EXCLUDED.wikipedia_status = 'failed'
                                                THEN entity_enrichment.wikipedia_extract
                                                ELSE EXCLUDED.wikipedia_extract END,
                    wikipedia_url        = CASE WHEN EXCLUDED.wikipedia_status = 'failed'
                                                THEN entity_enrichment.wikipedia_url
                                                ELSE EXCLUDED.wikipedia_url END,
                    wikidata_status      = EXCLUDED.wikidata_status,
                    wikidata_fetched_at  = EXCLUDED.wikidata_fetched_at,
                    wikipedia_status     = EXCLUDED.wikipedia_status,
                    wikipedia_fetched_at = EXCLUDED.wikipedia_fetched_at,
                    retry_after          = EXCLUDED.retry_after,
                    schema_version       = EXCLUDED.schema_version,
                    updated_at           = EXCLUDED.updated_at
                """,
                (
                    enrichment.entity_id,
                    enrichment.image_url,
                    enrichment.image_attribution,
                    enrichment.image_license,
                    enrichment.headline,
                    enrichment.wikipedia_extract,
                    enrichment.wikipedia_url,
                    facts,
                    affiliations,
                    enrichment.wikidata_status.value,
                    enrichment.wikidata_fetched_at,
                    enrichment.wikipedia_status.value,
                    enrichment.wikipedia_fetched_at,
                    enrichment.retry_after,
                    enrichment.schema_version,
                    enrichment.created_at,
                    now_dt,
                ),
            )

    def get_enrichment(self, entity_id: str) -> Optional[EntityEnrichment]:
        """Return the stored enrichment for an entity, or ``None``."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT * FROM entity_enrichment WHERE entity_id = %s",
                (entity_id,),
            ).fetchone()
        return _row_to_enrichment(row) if row else None

    def delete_enrichment(self, entity_id: str) -> bool:
        """Drop an entity's ``entity_enrichment`` row so it re-enriches."""
        with connect(self.dsn) as conn:
            cursor = conn.execute(
                "DELETE FROM entity_enrichment WHERE entity_id = %s",
                (entity_id,),
            )
            return cursor.rowcount > 0

    def entity_ids_needing_enrichment(
        self,
        *,
        entity_id: Optional[str] = None,
        episode_id: Optional[str] = None,
        podcast_id: Optional[str] = None,
        limit: Optional[int] = None,
        max_age_days: Optional[int] = None,
        schema_version: int = 1,
        force: bool = False,
    ) -> List[str]:
        """Resolved entities with a QID that should be (re)enriched."""
        now_dt = datetime.now(timezone.utc)
        where = ["ent.wikidata_qid IS NOT NULL"]
        scope_params: list = []
        if entity_id:
            where.append("ent.id = %s")
            scope_params.append(entity_id)
        if episode_id:
            where.append(
                "ent.id IN (SELECT entity_id FROM entity_mentions WHERE episode_id = %s AND entity_id IS NOT NULL)"
            )
            scope_params.append(episode_id)
        if podcast_id:
            where.append(
                "ent.id IN (SELECT entity_id FROM entity_mentions "
                "WHERE entity_id IS NOT NULL AND episode_id IN "
                "(SELECT id FROM episodes WHERE podcast_id = %s))"
            )
            scope_params.append(podcast_id)

        stale_params: list = []
        if force:
            stale_sql = "1=1"
        else:
            clauses = [
                "en.entity_id IS NULL",
                "en.schema_version < %s",
                "(en.wikidata_status = 'failed' AND (en.retry_after IS NULL OR en.retry_after <= %s))",
                "(en.wikipedia_status = 'failed' AND (en.retry_after IS NULL OR en.retry_after <= %s))",
            ]
            stale_params = [schema_version, now_dt, now_dt]
            if max_age_days is not None:
                cutoff = now_dt - timedelta(days=max_age_days)
                clauses.append("en.updated_at < %s")
                stale_params.append(cutoff)
            stale_sql = " OR ".join(clauses)

        sql = (
            "SELECT ent.id FROM entities ent "
            "LEFT JOIN entity_enrichment en ON en.entity_id = ent.id "
            f"WHERE {' AND '.join(where)} AND ({stale_sql}) "
            "ORDER BY ent.id"
        )
        params = scope_params + stale_params
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with connect(self.dsn) as conn:
            return [row["id"] for row in conn.execute(sql, params).fetchall()]

    # ------------------------------------------------------------------
    # Lookup / typeahead
    # ------------------------------------------------------------------

    def find_entity_by_name(self, name: str, *, entity_type: Optional[str] = None) -> Optional[EntityRecord]:
        """Resolve a free-form name (canonical name OR alias OR id) to an
        entity. Case-insensitive on canonical_name; exact match on id;
        alias match is a case-insensitive jsonb element scan (translates
        the intent of the SQLite JSON-text ``LIKE``).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE id = %s",
                (name,),
            ).fetchone()
            if row is None:
                sql = "SELECT * FROM entities WHERE LOWER(canonical_name) = LOWER(%s)"
                params: list = [name]
                if entity_type is not None:
                    sql += " AND type = %s"
                    params.append(entity_type)
                sql += " LIMIT 1"
                row = conn.execute(sql, params).fetchone()
            if row is None:
                sql = (
                    "SELECT * FROM entities WHERE EXISTS ("
                    "  SELECT 1 FROM jsonb_array_elements_text(entities.aliases) AS a(alias)"
                    "  WHERE LOWER(a.alias) = LOWER(%s)"
                    ")"
                )
                params = [name]
                if entity_type is not None:
                    sql += " AND type = %s"
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

        Same ranking as the SQLite version (role boost, then mention
        count, then name length/name). ``json_each`` role scans become
        ``jsonb_array_elements_text`` laterals; the substring match uses
        ``ILIKE`` (canonical name) and ``ILIKE`` on the jsonb text form
        of the aliases array.
        """
        prefix = (prefix or "").strip()
        if not prefix:
            return []
        like_pattern = f"%{prefix}%"
        type_clause = ""
        type_params: list = []
        if types:
            type_clause = "AND e.type = ANY(%s)"
            type_params = [list(types)]
        sql = f"""
            WITH role_index AS (
                SELECT g.value AS entity_id,
                       3 AS role_score, 'guest' AS role, episodes.id AS episode_id
                FROM episodes
                CROSS JOIN LATERAL jsonb_array_elements_text(episodes.guest_entity_ids) AS g(value)
                WHERE episodes.guest_entity_ids <> '[]'::jsonb
                UNION ALL
                SELECT h.value AS entity_id,
                       2 AS role_score, 'host' AS role, episodes.id AS episode_id
                FROM episodes
                JOIN podcasts ON podcasts.id = episodes.podcast_id
                CROSS JOIN LATERAL jsonb_array_elements_text(podcasts.host_entity_ids) AS h(value)
                WHERE podcasts.host_entity_ids <> '[]'::jsonb
                UNION ALL
                SELECT r.value AS entity_id,
                       1 AS role_score, 'recurring' AS role, episodes.id AS episode_id
                FROM episodes
                JOIN podcasts ON podcasts.id = episodes.podcast_id
                CROSS JOIN LATERAL jsonb_array_elements_text(podcasts.recurring_entity_ids) AS r(value)
                WHERE podcasts.recurring_entity_ids <> '[]'::jsonb
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
                WHERE (e.canonical_name ILIKE %s
                       OR e.aliases::text ILIKE %s)
                  {type_clause}
            )
            SELECT id, type, canonical_name, aliases, mention_count,
                   role_score, role_episode_count
            FROM ranked
            WHERE rn <= %s
            ORDER BY role_score DESC,
                     role_episode_count DESC,
                     mention_count DESC,
                     LENGTH(canonical_name) ASC,
                     canonical_name ASC
        """
        params = [like_pattern, like_pattern, *type_params, limit_per_type]
        with connect(self.dsn) as conn:
            rows = conn.execute(sql, params).fetchall()
        hits: List[EntityHit] = []
        prefix_lower = prefix.lower()
        for row in rows:
            aliases = row["aliases"] or []
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

        Same two-phase shape as the SQLite version: phase 1 computes the
        corpus-wide aggregate as a plain read; phase 2 is a short write
        transaction (DELETE + bulk INSERT) so readers never see a
        half-rebuilt scope. ``episode_ids=None`` is a full rebuild.
        Canonical pair ordering (``a < b``) matches the table CHECK.
        """
        # ---- Phase 1: read-only aggregate ----
        affected_ids: Optional[List[str]]
        with connect(self.dsn) as conn:
            if episode_ids is None:
                affected_ids = None
                affected_predicate = ""
                select_params: list = []
            else:
                if not episode_ids:
                    return 0
                affected_rows = conn.execute(
                    """
                    SELECT DISTINCT entity_id FROM entity_mentions
                    WHERE entity_id IS NOT NULL
                      AND resolution_status = 'resolved'
                      AND episode_id = ANY(%s)
                    """,
                    (list(episode_ids),),
                ).fetchall()
                affected_ids = [r["entity_id"] for r in affected_rows]
                if not affected_ids:
                    return 0
                affected_predicate = " AND (a.entity_id = ANY(%s) OR b.entity_id = ANY(%s))"
                select_params = [affected_ids, affected_ids]

            pair_rows = conn.execute(
                f"""
                SELECT
                    a.entity_id AS entity_a_id,
                    b.entity_id AS entity_b_id,
                    COUNT(DISTINCT a.episode_id) AS episode_count,
                    MAX(COALESCE(a.resolved_at, a.created_at)) AS last_seen_at
                FROM entity_mentions a
                JOIN entity_mentions b
                    ON a.episode_id = b.episode_id
                   AND a.entity_id < b.entity_id
                WHERE a.resolution_status = 'resolved'
                  AND b.resolution_status = 'resolved'
                  {affected_predicate}
                GROUP BY a.entity_id, b.entity_id
                """,
                select_params,
            ).fetchall()

        insert_values = [(r["entity_a_id"], r["entity_b_id"], r["episode_count"], r["last_seen_at"]) for r in pair_rows]

        # ---- Phase 2: short write transaction (DELETE + bulk INSERT) ----
        with connect(self.dsn) as conn:
            if affected_ids is None:
                conn.execute("DELETE FROM entity_cooccurrences")
            else:
                conn.execute(
                    "DELETE FROM entity_cooccurrences WHERE entity_a_id = ANY(%s) OR entity_b_id = ANY(%s)",
                    (affected_ids, affected_ids),
                )
            if insert_values:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO entity_cooccurrences (
                            entity_a_id, entity_b_id, episode_count, last_seen_at
                        ) VALUES (%s, %s, %s, %s)
                        """,
                        insert_values,
                    )
        inserted = len(insert_values)
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
        with connect(self.dsn) as conn:
            conn.execute(
                "UPDATE podcasts SET host_entity_ids = %s WHERE id = %s",
                (Jsonb(list(entity_ids)), podcast_id),
            )

    def set_podcast_recurring(self, podcast_id: str, entity_ids: List[str]) -> None:
        """Replace ``podcasts.recurring_entity_ids`` with the given list."""
        with connect(self.dsn) as conn:
            conn.execute(
                "UPDATE podcasts SET recurring_entity_ids = %s WHERE id = %s",
                (Jsonb(list(entity_ids)), podcast_id),
            )

    def set_episode_guests(self, episode_id: str, entity_ids: List[str]) -> None:
        """Replace ``episodes.guest_entity_ids`` with the given list."""
        with connect(self.dsn) as conn:
            conn.execute(
                "UPDATE episodes SET guest_entity_ids = %s WHERE id = %s",
                (Jsonb(list(entity_ids)), episode_id),
            )

    def get_podcast_anchors(self, podcast_id: str) -> dict:
        """Return ``{'hosts': [...], 'recurring': [...]}`` for one podcast."""
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT host_entity_ids, recurring_entity_ids FROM podcasts WHERE id = %s",
                (podcast_id,),
            ).fetchone()
        if row is None:
            return {"hosts": [], "recurring": []}
        return {
            "hosts": row["host_entity_ids"] or [],
            "recurring": row["recurring_entity_ids"] or [],
        }

    def get_episode_anchors(self, episode_id: str) -> List[str]:
        """Return the union of host + recurring + guest entity ids for an
        episode (order-preserving, de-duplicated).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT p.host_entity_ids, p.recurring_entity_ids,
                       e.guest_entity_ids
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.id = %s
                """,
                (episode_id,),
            ).fetchone()
        if row is None:
            return []
        ids: List[str] = []
        for column in ("host_entity_ids", "recurring_entity_ids", "guest_entity_ids"):
            ids.extend(row[column] or [])
        seen: set = set()
        unique: List[str] = []
        for entity_id in ids:
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                unique.append(entity_id)
        return unique

    def detect_top_speakers(self, podcast_id: str, *, limit: int = 5) -> List[Tuple[str, int]]:
        """Spec §1.13.1 — propose hosts by speaker frequency."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT m.speaker, COUNT(*) AS n
                FROM entity_mentions m
                JOIN episodes e ON m.episode_id = e.id
                WHERE e.podcast_id = %s
                  AND m.speaker IS NOT NULL
                  AND TRIM(m.speaker) != ''
                  AND LOWER(m.speaker) != 'unknown'
                GROUP BY m.speaker
                ORDER BY n DESC
                LIMIT %s
                """,
                (podcast_id, limit),
            ).fetchall()
        return [(r["speaker"], r["n"]) for r in rows]

    # ------------------------------------------------------------------
    # Spec #28 §1.13.5 — within-episode coreference helpers
    # ------------------------------------------------------------------

    def list_unresolved_person_mentions(self, episode_id: str) -> List[EntityMention]:
        """Mentions for one episode with surface_label='person' (or NULL)
        and ``resolution_status='unresolvable'``. Drives the coref pass.
        """
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT * FROM entity_mentions
                WHERE episode_id = %s
                  AND resolution_status = 'unresolvable'
                  AND (surface_label = 'person' OR surface_label IS NULL)
                """,
                (episode_id,),
            ).fetchall()
        return [_row_to_mention(r) for r in rows]

    def list_resolved_persons_for_episode(self, episode_id: str) -> List[EntityRecord]:
        """Distinct ``person``-typed entities resolved in this episode."""
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT e.*
                FROM entity_mentions m
                JOIN entities e ON m.entity_id = e.id
                WHERE m.episode_id = %s
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
        """Insert a row into ``mention_overrides``. Returns the row id
        (``RETURNING id`` replaces ``cursor.lastrowid``).
        """
        if kind not in ("drop", "force_entity", "force_unresolvable"):
            raise ValueError(f"invalid override kind={kind!r}")
        if kind == "force_entity" and not entity_id:
            raise ValueError("force_entity requires entity_id")
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                INSERT INTO mention_overrides
                    (surface_form, episode_id, override_kind, entity_id, reason, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (surface_form, episode_id, kind, entity_id, reason, created_by),
            ).fetchone()
            return int(row["id"])

    def lookup_override(self, surface_form: str, episode_id: Optional[str]) -> Optional[dict]:
        """Find an override matching ``(surface_form, episode_id)`` or
        ``(surface_form, NULL)`` (global). Episode-scoped wins over
        global (boolean sort: false < true, matching SQLite's 0 < 1).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT id, surface_form, episode_id, override_kind, entity_id,
                       reason, created_by, created_at
                FROM mention_overrides
                WHERE LOWER(surface_form) = LOWER(%s)
                  AND (episode_id = %s OR episode_id IS NULL)
                ORDER BY (episode_id IS NULL) ASC, id DESC
                LIMIT 1
                """,
                (surface_form, episode_id),
            ).fetchone()
        return _override_row_to_dict(row) if row else None

    def list_overrides(self, *, limit: int = 200) -> List[dict]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT * FROM mention_overrides ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [_override_row_to_dict(r) for r in rows]

    def get_mention(self, mention_id: int) -> Optional[EntityMention]:
        with connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT * FROM entity_mentions WHERE id = %s",
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
        """Negative cache: refuse to ground ``surface_form → wrong_qid``.

        ``INSERT OR IGNORE`` → ``ON CONFLICT DO NOTHING``; returns the
        new row id, or 0 when the pair already existed (matching the
        SQLite ``lastrowid or 0`` contract for ignored inserts).
        """
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                INSERT INTO resolution_blacklist
                    (surface_form, wrong_qid, reason)
                VALUES (%s, %s, %s)
                ON CONFLICT (surface_form, wrong_qid) DO NOTHING
                RETURNING id
                """,
                (surface_form, wrong_qid, reason),
            ).fetchone()
            return int(row["id"]) if row else 0

    def is_blacklisted(self, surface_form: str, wrong_qid: str) -> bool:
        with connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM resolution_blacklist
                WHERE LOWER(surface_form) = LOWER(%s) AND wrong_qid = %s
                LIMIT 1
                """,
                (surface_form, wrong_qid),
            ).fetchone()
        return row is not None

    def list_blacklist(self, *, limit: int = 200) -> List[dict]:
        with connect(self.dsn) as conn:
            rows = conn.execute(
                "SELECT * FROM resolution_blacklist ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Detection queue — review of likely-wrong resolutions
    # ------------------------------------------------------------------

    def fetch_resolution_review_rows(self) -> List[dict]:
        """Per ``(resolved entity, surface_form)`` aggregate feeding the
        review scan. ``GROUP BY e.id`` is legal (primary key ⇒ functional
        dependency). ``wikidata_instance_of`` is re-serialised to a JSON
        string because the consumer (``core.entity_review``) calls
        ``json.loads`` on it — that's the cross-backend contract.
        """
        with connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT e.id                   AS entity_id,
                       e.type                 AS type,
                       e.canonical_name       AS canonical_name,
                       e.wikidata_qid         AS wikidata_qid,
                       e.wikidata_instance_of AS wikidata_instance_of,
                       m.surface_form         AS surface_form,
                       COUNT(*)               AS mention_count
                FROM entities e
                JOIN entity_mentions m ON m.entity_id = e.id
                WHERE e.wikidata_qid IS NOT NULL
                  AND m.resolution_status = 'resolved'
                GROUP BY e.id, m.surface_form
                """
            ).fetchall()
        return [{**r, "wikidata_instance_of": json.dumps(r["wikidata_instance_of"] or [])} for r in rows]

    # ------------------------------------------------------------------
    # Alias merging (spec §1.6)
    # ------------------------------------------------------------------

    def find_duplicate_qid_pairs(self) -> List[Tuple[str, str, str]]:
        """Find pairs of entities sharing a Wikidata QID.

        Keeper rank: ``mention_count DESC, type_priority ASC, id ASC`` —
        same rule (and same Python-side ranking) as the SQLite version.
        """
        with connect(self.dsn) as conn:
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

        by_qid: Dict[str, List[dict]] = {}
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
        """Spec #28 §1.6 follow-up — entities whose stored type disagrees
        with the majority surface_label of their mentions. Same
        thresholds and Python-side majority logic as the SQLite version.
        """
        valid_types = {t.value for t in EntityType}
        with connect(self.dsn) as conn:
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


# ---------------------------------------------------------------------------
# Row converters — dict rows (psycopg dict_row): jsonb columns are already
# list/dict, timestamptz columns are tz-aware datetimes, uuid columns need
# ``as_str``. Entity ids are slugs and come back as plain str.
# ---------------------------------------------------------------------------


def _row_to_entity(row: dict) -> EntityRecord:
    return EntityRecord(
        id=row["id"],
        type=EntityType(row["type"]),
        canonical_name=row["canonical_name"],
        wikidata_qid=row["wikidata_qid"],
        aliases=row["aliases"] or [],
        description=row["description"],
        wikidata_instance_of=row["wikidata_instance_of"] or [],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_mention(row: dict) -> EntityMention:
    method_str = row.get("resolution_method")
    return EntityMention(
        id=row["id"],
        entity_id=row["entity_id"],
        resolution_status=ResolutionStatus(row["resolution_status"]),
        episode_id=as_str(row["episode_id"]),
        segment_id=row["segment_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        speaker=row["speaker"],
        role=MentionRole(row["role"]) if row["role"] else None,
        surface_form=row["surface_form"],
        surface_label=row.get("surface_label"),
        quote_excerpt=row["quote_excerpt"],
        sentiment=row["sentiment"],
        confidence=row["confidence"],
        extractor=row["extractor"],
        resolution_method=ResolutionMethod(method_str) if method_str else None,
        candidate_entity_ids=row.get("candidate_entity_ids") or [],
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
    )


def _row_to_mention_context(row: dict) -> MentionContext:
    return MentionContext(
        mention=_row_to_mention(row),
        episode_id=as_str(row["episode_id"]),
        episode_title=row["episode_title"],
        episode_pub_date=row["episode_pub_date"],
        podcast_id=as_str(row["podcast_id"]),
        podcast_title=row["podcast_title"],
        podcast_slug=row["podcast_slug"],
        entity_type=row["entity_type"],
        entity_canonical_name=row["entity_canonical_name"],
    )


def _row_to_enrichment(row: dict) -> EntityEnrichment:
    facts = [EntityFact(**f) for f in (row.get("facts_json") or [])]
    affiliations = [EntityAffiliation(**a) for a in (row.get("affiliations_json") or [])]
    now = datetime.now(timezone.utc)
    return EntityEnrichment(
        entity_id=row["entity_id"],
        image_url=row.get("image_url"),
        image_attribution=row.get("image_attribution"),
        image_license=row.get("image_license"),
        headline=row.get("headline"),
        wikipedia_extract=row.get("wikipedia_extract"),
        wikipedia_url=row.get("wikipedia_url"),
        facts=facts,
        affiliations=affiliations,
        wikidata_status=EnrichmentStatus(row.get("wikidata_status") or "pending"),
        wikidata_fetched_at=row.get("wikidata_fetched_at"),
        wikipedia_status=EnrichmentStatus(row.get("wikipedia_status") or "pending"),
        wikipedia_fetched_at=row.get("wikipedia_fetched_at"),
        retry_after=row.get("retry_after"),
        schema_version=row.get("schema_version") or 1,
        created_at=row.get("created_at") or now,
        updated_at=row.get("updated_at") or now,
    )


def _override_row_to_dict(row: dict) -> dict:
    """mention_overrides row → dict with the uuid episode_id stringified."""
    out = dict(row)
    out["episode_id"] = as_str(out.get("episode_id"))
    return out
