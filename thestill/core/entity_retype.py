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

"""Re-bucket an existing entity from its Wikidata P31 set, keeping its id.

Spec #92 Phase 0. An entity id is held by more than mentions: cooccurrences,
enrichment, overrides, the host/guest/recurring anchor lists, mention
candidate lists and every ``/entities/<id>`` link already shared. Changing
the type therefore changes the row, never the id; the #296 lookup fallback
already serves an id whose prefix differs from its type, and the live
linker keeps an existing row's id for a known QID the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, List, Optional

from ..models.entities import EntityRecord, EntityType
from .entity_type_rules import PERSON_P31, WORK_P31, classify_entity_type


def plan_retype(entity: EntityRecord, p31: List[str], *, only_works: bool = False) -> Optional[EntityType]:
    """The type ``entity`` should have given ``p31``, or ``None`` if unchanged.

    ``only_works`` limits the change to the spec #92 works rule: a film,
    series, book, podcast, album or game becomes a product, and nothing
    else moves. The other P31 rules still demote real companies whose
    Wikidata classes include the generic "organization", so a corpus-wide
    run of all of them is not safe yet.
    """
    if only_works:
        works = set(p31) & WORK_P31 and not set(p31) & PERSON_P31
        return EntityType.PRODUCT if works and entity.type != EntityType.PRODUCT else None
    classified = classify_entity_type(p31, entity.type) or entity.type
    return None if classified == entity.type else classified


@dataclass(frozen=True)
class RetypePlan:
    entity: EntityRecord
    p31: List[str]
    new_type: Optional[EntityType]  # None: type unchanged, P31 cache only

    @property
    def caches_p31(self) -> bool:
        return self.entity.wikidata_instance_of != self.p31


def plan_backfill(
    entities: Iterable[EntityRecord],
    fetch_p31: Optional[Callable[[str], List[str]]],
    *,
    only_works: bool = False,
) -> Iterator[RetypePlan]:
    """What ``backfill-entity-types`` would do, one entity at a time.

    ``fetch_p31`` is called only for entities with no stored P31; ``None``
    (``--cached-only``) skips them instead, so the run makes no network
    calls. Entities with nothing to change are not yielded.
    """
    for entity in entities:
        p31 = entity.wikidata_instance_of
        if not p31:
            if fetch_p31 is None:
                continue
            p31 = fetch_p31(entity.wikidata_qid)
            if not p31:
                continue
        new_type = plan_retype(entity, p31, only_works=only_works)
        if new_type is None and entity.wikidata_instance_of == p31:
            continue
        yield RetypePlan(entity, p31, new_type)


def retype_in_place(repo, entity: EntityRecord, new_type: EntityType, p31: List[str]) -> None:
    """Store ``new_type`` (and the P31 cache) on the existing row.

    ``upsert_entity`` overwrites ``type`` on both backends and merges
    everything else, so the id, aliases, QID and description survive.
    """
    repo.upsert_entity(entity.model_copy(update={"type": new_type, "wikidata_instance_of": p31}))
