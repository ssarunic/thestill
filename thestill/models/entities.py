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

"""Entity-layer models for spec #28 (corpus search & entities).

Four user-facing models:

- ``EntityRecord``  — a canonical person/company/product/topic.
- ``EntityMention`` — a single occurrence of an entity in an episode segment.
- ``CitationRow``   — the wire shape every search/list tool returns.

Plus five enums (``EntityType``, ``ResolutionStatus``,
``EntityExtractionStatus``, ``MentionRole``, ``MatchType``) backing
the ``CHECK``-constrained string columns in SQLite.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class EntityType(str, Enum):
    PERSON = "person"
    COMPANY = "company"
    PRODUCT = "product"
    TOPIC = "topic"


class ResolutionStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    UNRESOLVABLE = "unresolvable"
    # Spec #28 §1.13.5: a short-form mention with multiple long-form
    # candidates in the same episode. Surfaced in the admin override
    # queue rather than guessed.
    AMBIGUOUS = "ambiguous"
    # Spec #28 §1.13.7: a human override said "drop this mention"
    # (e.g. generic noun mistakenly extracted). Survives reindex via
    # ``mention_overrides``.
    DROPPED = "dropped"


class EntityExtractionStatus(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED_LEGACY = "skipped_legacy"


class MentionRole(str, Enum):
    HOST = "host"
    GUEST = "guest"
    MENTIONED = "mentioned"
    SELF = "self"
    # Spec #28 §1.13.2: a synthesized mention of the segment's speaker
    # (so ``list_quotes_by`` finds them even when they don't say their
    # own name). Distinct from SELF, which marks a self-reference inside
    # body text ("I, Karpathy, think...").
    SPEAKING = "speaking"


class ResolutionMethod(str, Enum):
    """Spec #28 §1.13.6 — how a mention reached its current status.

    Persisted alongside ``resolution_status`` so downstream consumers
    can downweight or flag less-authoritative resolutions.
    """

    DIRECT = "direct"  # ReFinED grounded the surface to a QID
    ANCHOR = "anchor"  # matched a host/guest/recurring anchor variant
    COREF = "coref"  # within-episode coreference (post-resolve pass)
    OVERRIDE = "override"  # forced by a human via mention_overrides
    UNRESOLVABLE = "unresolvable"  # ReFinED failed or scored too low
    AMBIGUOUS = "ambiguous"  # multiple candidates in same episode


class MatchType(str, Enum):
    LEXICAL = "lexical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    ENTITY = "entity"


class EntityRecord(BaseModel):
    """A canonical entity (person/company/product/topic).

    ``id`` is a slug-typed string of the form ``"{type}:{slug}"`` — e.g.
    ``"person:elon-musk"`` — *not* a UUID. The slug portion is generated
    from ``canonical_name`` via ``thestill.utils.slug.generate_slug``.

    ``wikidata_qid`` is the resolution target where one exists (e.g.
    ``"Q317521"``). ``None`` for entities that ReFinED could not map and
    that the local alias-fallback created with a slug-only id.
    """

    id: str
    type: EntityType
    canonical_name: str
    wikidata_qid: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EntityMention(BaseModel):
    """One entity occurrence inside one episode segment.

    Written by ``extract-entities`` with ``entity_id=None`` and
    ``resolution_status=PENDING``; ``resolve-entities`` later fills both
    in. ``segment_id`` is the positional id from the
    ``AnnotatedTranscript`` JSON sidecar — the source of truth for
    transcript timing — multiplied by 1000 (seconds → ms) for the
    ``start_ms``/``end_ms`` fields.

    ``id`` is the SQLite ``AUTOINCREMENT`` primary key; ``None`` before
    the row is inserted.
    """

    id: Optional[int] = None
    entity_id: Optional[str] = None
    resolution_status: ResolutionStatus = ResolutionStatus.PENDING
    episode_id: str
    segment_id: int
    start_ms: int
    end_ms: int
    speaker: Optional[str] = None
    role: Optional[MentionRole] = None
    surface_form: str
    # Spec #28 §1.5 — the GLiNER label that produced this mention
    # (``person`` / ``company`` / ``product`` / ``topic``). Lets the
    # resolver map to ``EntityType`` without re-running extraction.
    # ``None`` for legacy rows written before the surface_label column
    # existed; resolver falls back to ReFinED's ``coarse_type`` then.
    surface_label: Optional[str] = None
    quote_excerpt: str
    sentiment: Optional[float] = None
    confidence: float
    extractor: str
    # Spec #28 §1.13.6 — how the resolver landed on this row. ``None``
    # for legacy mentions written before the column existed.
    resolution_method: Optional[ResolutionMethod] = None
    # Spec #28 §1.13.5 — populated only when ``resolution_status='ambiguous'``.
    # JSON list of entity ids that matched the short-form mention; the
    # admin override queue picks one.
    candidate_entity_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


class CitationRow(BaseModel):
    """Citation-shaped wire row returned by every search/list tool.

    Per Strategy §4: no tool returns a ``summarised`` field without the
    source attached. ``deeplink`` is the in-app ``thestill://`` URI;
    ``web_url`` is the equivalent web path with a timestamp anchor.
    """

    episode_id: str
    podcast_id: str
    podcast_title: str
    episode_title: str
    published_at: Optional[datetime] = None
    start_ms: int
    end_ms: int
    speaker: Optional[str] = None
    quote: str
    score: float
    match_type: MatchType
    deeplink: str
    web_url: str
