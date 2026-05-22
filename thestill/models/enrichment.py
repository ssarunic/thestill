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

"""Entity enrichment models for spec #45 (entity-page enrichment) — Tier 0.

Additive *display* data for an entity page, fetched from Wikidata +
Wikipedia and gated on the entity carrying a Wikidata QID. Enrichment is
never part of the processing critical path: it is fetched in batch by
``thestill enrich-entities``, persisted 1:1 in the ``entity_enrichment``
table, and surfaced (nullable) on the entity-page API.

Three user-facing models:

- ``EntityFact``        — one labelled vital-stat row in the sidebar.
- ``EntityAffiliation`` — a person↔organisation link (founder, CEO,
  employer), cross-linked to a local entity page when the referenced
  QID matches an entity we already hold.
- ``EntityEnrichment``  — the full per-entity record, with per-source
  provenance so a transient fetch failure is never cached as "no data"
  (spec #42 FM-1).

``EnrichmentUnavailable`` is the shared contract between the fetch
clients (which raise it on a *transient* failure) and the enricher
(which maps it to ``EnrichmentStatus.FAILED`` + a ``retry_after``).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class EnrichmentUnavailable(Exception):
    """A source could not be reached or returned an unusable response.

    Raised by the Wikidata / Wikipedia clients for *transient* failures
    (network error, non-200, unparseable body). Distinct from a
    successful fetch that simply found nothing — that returns an empty
    result, which the enricher records as ``EnrichmentStatus.EMPTY``.
    Keeping the two apart is the spec #42 FM-1 lesson: a 503 must never
    be cached as "this entity has no photo".
    """


class EnrichmentStatus(str, Enum):
    """Per-source fetch outcome, persisted alongside the data."""

    PENDING = "pending"  # not yet attempted
    OK = "ok"  # fetched successfully (content may still be partial)
    EMPTY = "empty"  # fetched OK, source genuinely had nothing
    FAILED = "failed"  # transient failure; retry after ``retry_after``


class EntityFact(BaseModel):
    """One labelled vital-stat row (e.g. ``Born`` → ``June 28, 1971``).

    ``url`` turns the value into a link (websites, social handles).
    Cross-linking a referenced entity to its own page is handled by
    :class:`EntityAffiliation`, not here.
    """

    label: str
    value: str
    url: Optional[str] = None


class EntityAffiliation(BaseModel):
    """A person↔organisation relationship surfaced as a cross-link chip.

    ``relation`` is the human label of the edge (``Founder``, ``CEO``,
    ``Works at``). ``entity_id`` / ``entity_type`` are set when the
    referenced QID resolves to a local entity, so the frontend links to
    its page; otherwise the chip renders as plain text.
    """

    qid: Optional[str] = None
    label: str
    relation: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None


class EntityEnrichment(BaseModel):
    """Tier-0 enrichment for a single entity (spec #45).

    Persisted 1:1 in ``entity_enrichment`` (keyed by ``entity_id`` so it
    survives an entity reindex, like ``mention_overrides``). The
    ``*_status`` / ``*_fetched_at`` fields record per-source provenance;
    ``retry_after`` gates the next attempt for a source that ``FAILED``;
    ``schema_version`` lets a logic change invalidate stale rows on the
    next run.
    """

    entity_id: str

    # Hero / about
    image_url: Optional[str] = None
    image_attribution: Optional[str] = None
    image_license: Optional[str] = None
    headline: Optional[str] = None  # Wikidata one-line description
    wikipedia_extract: Optional[str] = None
    wikipedia_url: Optional[str] = None

    # Sidebar + cross-links
    facts: List[EntityFact] = Field(default_factory=list)
    affiliations: List[EntityAffiliation] = Field(default_factory=list)

    # Provenance — see class docstring (spec #42 FM-1).
    wikidata_status: EnrichmentStatus = EnrichmentStatus.PENDING
    wikidata_fetched_at: Optional[datetime] = None
    wikipedia_status: EnrichmentStatus = EnrichmentStatus.PENDING
    wikipedia_fetched_at: Optional[datetime] = None
    retry_after: Optional[datetime] = None
    schema_version: int = 1

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def has_content(self) -> bool:
        """True when there is anything worth rendering on the page."""
        return bool(self.image_url or self.headline or self.wikipedia_extract or self.facts or self.affiliations)
