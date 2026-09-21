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

"""What every entity linker shares (spec #81).

Moved out of ``entity_resolver.py`` so the ReFinED resolver and the live
Wikidata linker both import from here, and so removing ReFinED later
deletes one module without touching this package. ``entity_resolver``
re-exports these names for its existing import sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

from ...models.entities import EntityMention, EntityRecord, EntityType, ResolutionMethod
from ...utils.slug import generate_slug


class _P31Lookup(Protocol):
    """Structural type matching :class:`thestill.core.wikidata_client.WikidataClient`.

    Lets the resolver accept either the real client or the
    ``NullWikidataClient`` test stub without an import-time dependency.
    """

    def fetch_p31(self, qid: str) -> List[str]: ...


# Map GLiNER's surface_label (the Phase 1.2 extractor label) to
# ``EntityType``. Direct one-to-one — the four types are the same.
SURFACE_LABEL_TO_ENTITY_TYPE = {
    "person": EntityType.PERSON,
    "company": EntityType.COMPANY,
    "product": EntityType.PRODUCT,
    "topic": EntityType.TOPIC,
}


@dataclass(frozen=True)
class ResolutionResult:
    """One mention's resolution output.

    ``entity`` is the canonical ``EntityRecord`` to upsert into the
    ``entities`` table. ``mention_id`` and ``status`` drive the
    per-row ``resolve_mention`` UPDATE. When ``status='unresolvable'``
    the ``entity`` is still populated — the handler creates a local
    slug-only entity (no QID) so future occurrences of the same
    surface form can be merged into it.

    ``method`` records *how* the resolver landed on this entity (spec
    §1.13.6) — ``direct`` for ReFinED hits, ``override`` when a
    persisted ``mention_overrides`` row forced the answer,
    ``unresolvable`` when the threshold rejected the QID, etc.
    """

    mention_id: int
    entity: EntityRecord
    status: str  # "resolved" | "unresolvable"
    method: ResolutionMethod = ResolutionMethod.DIRECT


class EntityLinkerBrokenError(RuntimeError):
    """The linker is failing systematically; nothing from this batch was recorded.

    ``unresolvable`` is a terminal status nothing revisits, so a linker that
    fails on most of a batch must raise rather than write its failures off
    as "not in Wikidata" (failure-mode catalogue: errors-as-empty-results).
    """


def infer_entity_type_from_label(mention: EntityMention) -> EntityType:
    """The GLiNER label persisted with the mention, or ``topic``."""
    if mention.surface_label:
        mapped = SURFACE_LABEL_TO_ENTITY_TYPE.get(mention.surface_label.lower())
        if mapped is not None:
            return mapped
    return EntityType.TOPIC


def unresolvable_result(mention: EntityMention) -> ResolutionResult:
    """Build the local-slug fallback entity.

    Spec #28 §1.5: "create local ``entity_id`` for unresolved entities
    (slugified surface form)." The mention's ``resolution_status`` flips to
    ``unresolvable`` but we still produce an ``EntityRecord`` so future
    occurrences of the same surface form land in the same local entity.
    """
    entity_type = infer_entity_type_from_label(mention)
    entity_id = _build_entity_id(entity_type, mention.surface_form, qid=None)
    return ResolutionResult(
        mention_id=mention.id,  # type: ignore[arg-type]  # always set when read from DB
        entity=EntityRecord(
            id=entity_id,
            type=entity_type,
            canonical_name=mention.surface_form,
            wikidata_qid=None,
            aliases=[],
        ),
        status="unresolvable",
        method=ResolutionMethod.UNRESOLVABLE,
    )


def _is_plausible_alias(surface_form: str, canonical_name: str) -> bool:
    """Defense-in-depth: only persist ``surface_form`` as an alias of
    ``canonical_name`` when the two share lexical content. Stops the
    resolver from quietly recording wildly unrelated phrases as
    aliases when ReFinED returns a low-confidence match — historical
    contamination case: ``"consumer preferences"`` was stored as an
    alias of ``Henry Ford`` because both phrases co-occurred in one
    excerpt. After the ``_pick_best_span`` fix this should not happen
    in the first place; this guard is the second line of defense.

    Returns ``True`` when the alias is worth keeping:
    - one is a substring of the other (case-insensitive), OR
    - they share at least one whitespace token

    Returns ``False`` for identical strings (no alias needed) and
    for empty strings.
    """
    s = surface_form.lower().strip()
    c = canonical_name.lower().strip()
    if not s or not c or s == c:
        return False
    if s in c or c in s:
        return True
    return bool(set(s.split()) & set(c.split()))


def _build_entity_id(entity_type: EntityType, canonical_name: str, qid: Optional[str]) -> str:
    """Produce ``"{type}:{slug}"``.

    Slug source preference:
    1. Slug of canonical_name when it produces something useful
    2. ``q{qid}`` when slug degrades to ``unnamed`` (unicode-only
       surface forms transliterate to empty)
    3. ``q{qid}`` directly (lowercase) when ``canonical_name`` is
       empty or whitespace-only

    The QID-derived id keeps disambiguation stable across surface-form
    changes — re-resolving the same canonical entity later doesn't
    create a duplicate row even if its ``canonical_name`` shifted.
    """
    base_slug = generate_slug(canonical_name)
    if base_slug == "unnamed" and qid:
        base_slug = qid.lower()
    return f"{entity_type.value}:{base_slug}"
