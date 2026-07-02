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

"""Abstract repository interface for the spec #28 entity layer (spec #44).

Codifies the public contract of ``SqliteEntityRepository`` so the
PostgreSQL implementation (``PostgresEntityRepository``) can be swapped in
behind the same seam. The two lightweight projection dataclasses the
query methods return (``EntityHit``, ``MentionContext``) live here —
they are part of the contract, not of any one dialect. They are
re-exported from ``sqlite_entity_repository`` for backwards
compatibility with existing call sites.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from ..models.enrichment import EntityEnrichment
from ..models.entities import EntityMention, EntityRecord


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


class EntityRepository(ABC):
    """Abstract contract for ``entities`` / ``entity_mentions`` /
    ``entity_cooccurrences`` / ``entity_enrichment`` /
    ``mention_overrides`` / ``resolution_blacklist`` persistence.

    Implementations must be thread-safe (connection-per-operation) and
    must NOT own the DDL — schema bootstrap lives with the podcast
    repository (SQLite) / ``postgres_schema.ensure_schema`` (Postgres).
    """

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    @abstractmethod
    def upsert_entity(self, entity: EntityRecord) -> str:
        """Create or update an ``entities`` row atomically; return its id.

        On conflict: ``type``/``canonical_name`` are replaced,
        ``wikidata_qid``/``description`` only fill in when previously
        NULL, aliases are merged (distinct union, sorted),
        ``wikidata_instance_of`` is replaced only when the incoming
        list is non-empty, and ``updated_at`` advances.
        """

    @abstractmethod
    def get_entity(self, entity_id: str) -> Optional[EntityRecord]:
        """Look up by canonical ``"{type}:{slug}"`` id; ``None`` if absent."""

    @abstractmethod
    def find_entity_by_qid(self, wikidata_qid: str) -> Optional[EntityRecord]:
        """Look up by Wikidata QID (used during resolution merging)."""

    @abstractmethod
    def list_entities_by_type(self, entity_type: str) -> List[EntityRecord]:
        """Every entity of the given type, ordered by canonical_name."""

    @abstractmethod
    def delete_entity(self, entity_id: str) -> bool:
        """Hard-delete an entity (cascades to mentions/cooccurrences).

        Returns True if a row was deleted.
        """

    @abstractmethod
    def repoint_mentions(self, *, from_entity_id: str, to_entity_id: str) -> int:
        """Bulk re-point every mention of one entity at another; return
        the number of mentions updated. Used by alias-merge before
        deleting the loser of a duplicate pair.
        """

    # ------------------------------------------------------------------
    # Mentions
    # ------------------------------------------------------------------

    @abstractmethod
    def insert_mentions(self, mentions: Iterable[EntityMention]) -> int:
        """Bulk-insert mention rows; return rowcount. Input ``id`` values
        are ignored (the DB assigns them). Empty input is a 0 no-op.
        """

    @abstractmethod
    def delete_mentions_for_episode(self, episode_id: str) -> int:
        """Wipe all mentions for one episode (idempotent re-extraction);
        return rowcount.
        """

    @abstractmethod
    def count_mentions_for_episode(self, episode_id: str) -> int:
        """Diagnostic count of mentions for one episode."""

    @abstractmethod
    def list_pending_mentions(
        self,
        *,
        episode_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[EntityMention]:
        """Mentions with ``resolution_status='pending'``, ordered by id.

        ``episode_id`` scopes to one episode; without it the full
        backlog is returned (up to ``limit``).
        """

    @abstractmethod
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

        ``status`` must be one of ``resolved`` | ``unresolvable`` |
        ``ambiguous`` | ``dropped`` (``ValueError`` otherwise).
        ``method`` only overwrites when non-``None``. Returns True if a
        row was updated.
        """

    @abstractmethod
    def find_mention_ids_by_surface(
        self,
        surface_form: str,
        *,
        episode_id: Optional[str] = None,
        statuses: Tuple[str, ...] = ("resolved", "unresolvable", "ambiguous"),
    ) -> List[Tuple[int, str]]:
        """``(mention_id, episode_id)`` rows for mentions of
        ``surface_form`` (case-insensitive) in the given statuses.
        Empty ``statuses`` returns ``[]``.
        """

    @abstractmethod
    def reset_mentions_to_pending(self, mention_ids: List[int]) -> int:
        """Re-open mentions for re-resolution: status back to
        ``pending``, ``entity_id``/``resolved_at``/``resolution_method``/
        ``candidate_entity_ids`` cleared. Returns rowcount.
        """

    @abstractmethod
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
        """Resolved mentions joined with episode + podcast + entity,
        newest episode first (spec #28 §1.8 ``find_mentions``).
        All filters compose (AND).
        """

    @abstractmethod
    def list_mentions_by_speaker(
        self,
        *,
        speaker: str,
        topic_entity_id: Optional[str] = None,
        podcast_id: Optional[str] = None,
        date_range: Optional[Tuple[datetime, datetime]] = None,
        limit: int = 50,
    ) -> List["MentionContext"]:
        """Resolved mentions whose ``speaker`` matches (case-insensitive
        substring). ``topic_entity_id`` requires the topic to be
        resolved in the SAME diarisation segment (spec #28 §1.8
        ``list_quotes_by``).
        """

    @abstractmethod
    def get_mention_for_clip(
        self,
        *,
        episode_id: str,
        start_ms: int,
        end_ms: Optional[int] = None,
    ) -> Optional["MentionContext"]:
        """The resolved mention straddling ``start_ms`` for the episode,
        or the nearest one by absolute distance if none straddles.
        """

    # ------------------------------------------------------------------
    # Entity-page assembly (spec §1.8 — get_entity)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_entity_summary(
        self,
        entity_id: str,
        *,
        cooccurring_limit: int = 20,
        recent_mentions_limit: int = 10,
        most_discussed_limit: int = 10,
    ) -> Optional[dict]:
        """Entity + mention_count + cooccurring + recent_mentions +
        roles + most_discussed_on + enrichment (spec #28 §1.8 /
        spec #45). ``None`` if the entity doesn't exist.
        """

    @abstractmethod
    def get_entity_roles(
        self,
        entity_id: str,
        *,
        guest_episodes_limit: int = 50,
    ) -> dict:
        """``{'hosts_podcasts': [...], 'recurring_podcasts': [...],
        'guest_episodes': [...]}`` derived from the anchor id-lists on
        podcasts/episodes (spec #28 §1.13.1).
        """

    # ------------------------------------------------------------------
    # Enrichment (spec #45 Tier 0)
    # ------------------------------------------------------------------

    @abstractmethod
    def upsert_enrichment(self, enrichment: EntityEnrichment) -> None:
        """Persist a Tier-0 enrichment row. On update, content from a
        source that FAILED this run is preserved rather than wiped
        (spec #42 FM-1); status/timestamps/retry_after always advance;
        ``created_at`` is preserved.
        """

    @abstractmethod
    def get_enrichment(self, entity_id: str) -> Optional[EntityEnrichment]:
        """The stored enrichment for an entity, or ``None``."""

    @abstractmethod
    def delete_enrichment(self, entity_id: str) -> bool:
        """Drop an entity's enrichment row so it re-enriches from
        scratch. Returns True if a row was removed.
        """

    @abstractmethod
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
        """QID-bearing entities that should be (re)enriched: never
        enriched, older ``schema_version``, a failed source whose
        ``retry_after`` elapsed, or older than ``max_age_days``.
        ``force`` ignores staleness gating. Ordered by entity id.
        """

    # ------------------------------------------------------------------
    # Lookup / typeahead
    # ------------------------------------------------------------------

    @abstractmethod
    def find_entity_by_name(self, name: str, *, entity_type: Optional[str] = None) -> Optional[EntityRecord]:
        """Resolve a free-form name to an entity: exact id match, then
        case-insensitive canonical_name, then alias-element match.
        """

    @abstractmethod
    def search_entities_by_prefix(
        self,
        prefix: str,
        *,
        types: Optional[Tuple[str, ...]] = None,
        limit_per_type: int = 5,
    ) -> List["EntityHit"]:
        """Spec #28 §4.1 — ⌘K typeahead. Case-insensitive substring
        match on canonical_name/aliases, ranked by role boost
        (guest > host > recurring), then mention count, capped per type.
        """

    # ------------------------------------------------------------------
    # Co-occurrences
    # ------------------------------------------------------------------

    @abstractmethod
    def rebuild_cooccurrences(self, *, episode_ids: Optional[List[str]] = None) -> int:
        """Recompute ``entity_cooccurrences`` (corpus-wide per-pair
        distinct-episode counts) for pairs touched by the given
        episodes; ``None`` is a full wipe-and-rebuild. Returns the
        number of pair rows materialised.
        """

    # ------------------------------------------------------------------
    # Spec #28 §1.13.1 — host / guest / recurring anchor metadata
    # ------------------------------------------------------------------

    @abstractmethod
    def set_podcast_hosts(self, podcast_id: str, entity_ids: List[str]) -> None:
        """Replace ``podcasts.host_entity_ids`` with the given list."""

    @abstractmethod
    def set_podcast_recurring(self, podcast_id: str, entity_ids: List[str]) -> None:
        """Replace ``podcasts.recurring_entity_ids`` with the given list."""

    @abstractmethod
    def set_episode_guests(self, episode_id: str, entity_ids: List[str]) -> None:
        """Replace ``episodes.guest_entity_ids`` with the given list."""

    @abstractmethod
    def get_podcast_anchors(self, podcast_id: str) -> dict:
        """``{'hosts': [...], 'recurring': [...]}`` for one podcast
        (empty lists when the podcast is missing).
        """

    @abstractmethod
    def get_episode_anchors(self, episode_id: str) -> List[str]:
        """Union of host + recurring + guest entity ids for an episode,
        order-preserving and de-duplicated.
        """

    @abstractmethod
    def detect_top_speakers(self, podcast_id: str, *, limit: int = 5) -> List[Tuple[str, int]]:
        """``(speaker_label, segment_count)`` rows by frequency
        descending, excluding blank/'unknown' labels (spec §1.13.1).
        """

    # ------------------------------------------------------------------
    # Spec #28 §1.13.5 — within-episode coreference helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def list_unresolved_person_mentions(self, episode_id: str) -> List[EntityMention]:
        """Unresolvable mentions for one episode with
        ``surface_label='person'`` (or NULL). Drives the coref pass.
        """

    @abstractmethod
    def list_resolved_persons_for_episode(self, episode_id: str) -> List[EntityRecord]:
        """Distinct ``person``-typed entities resolved in this episode."""

    # ------------------------------------------------------------------
    # Spec #28 §1.13.7 — mention overrides + resolution blacklist
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Insert a ``mention_overrides`` row; return its id.

        ``kind`` must be ``drop`` | ``force_entity`` |
        ``force_unresolvable`` (``ValueError`` otherwise);
        ``force_entity`` requires ``entity_id``.
        """

    @abstractmethod
    def lookup_override(self, surface_form: str, episode_id: Optional[str]) -> Optional[dict]:
        """Best-match override for ``(surface_form, episode_id)`` —
        case-insensitive on surface; episode-scoped beats global;
        newest wins. Row as dict, or ``None``.
        """

    @abstractmethod
    def list_overrides(self, *, limit: int = 200) -> List[dict]:
        """Most recent overrides, newest first."""

    @abstractmethod
    def get_mention(self, mention_id: int) -> Optional[EntityMention]:
        """One mention by primary key, or ``None``."""

    @abstractmethod
    def add_blacklist_entry(
        self,
        *,
        surface_form: str,
        wrong_qid: str,
        reason: Optional[str] = None,
    ) -> int:
        """Negative cache: refuse ``surface_form → wrong_qid``.

        Idempotent on the ``(surface_form, wrong_qid)`` unique pair.
        """

    @abstractmethod
    def is_blacklisted(self, surface_form: str, wrong_qid: str) -> bool:
        """True iff ``(surface_form, wrong_qid)`` is blacklisted
        (surface match is case-insensitive).
        """

    @abstractmethod
    def list_blacklist(self, *, limit: int = 200) -> List[dict]:
        """Most recent blacklist entries, newest first."""

    # ------------------------------------------------------------------
    # Detection queue / alias merging (spec §1.6)
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch_resolution_review_rows(self) -> List[dict]:
        """Per ``(resolved QID-bearing entity, surface_form)`` aggregate
        rows feeding the review scan. Each dict carries ``entity_id,
        type, canonical_name, wikidata_qid, wikidata_instance_of``
        (JSON string), ``surface_form, mention_count``.
        """

    @abstractmethod
    def find_duplicate_qid_pairs(self) -> List[Tuple[str, str, str]]:
        """``(qid, keeper_id, loser_id)`` for entities sharing a QID.

        Keeper rank: mention_count DESC, ``EntityType`` declaration
        order ASC, id ASC.
        """

    @abstractmethod
    def find_mistyped_entities(
        self,
        *,
        min_mentions: int = 3,
        min_majority_ratio: float = 0.6,
    ) -> List[Tuple[str, str, str, int, int]]:
        """``(entity_id, current_type, suggested_type, majority_count,
        total_count)`` for entities whose stored type disagrees with
        the majority mention ``surface_label``, gated by the minimum
        signal thresholds. Sorted by majority_count DESC, entity_id.
        """
