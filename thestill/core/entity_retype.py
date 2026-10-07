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

from typing import List, Optional

from ..models.entities import EntityRecord, EntityType
from .entity_type_rules import classify_entity_type


def plan_retype(entity: EntityRecord, p31: List[str]) -> Optional[EntityType]:
    """The type ``entity`` should have given ``p31``, or ``None`` if unchanged."""
    classified = classify_entity_type(p31, entity.type) or entity.type
    return None if classified == entity.type else classified


def retype_in_place(repo, entity: EntityRecord, new_type: EntityType, p31: List[str]) -> None:
    """Store ``new_type`` (and the P31 cache) on the existing row.

    ``upsert_entity`` overwrites ``type`` on both backends and merges
    everything else, so the id, aliases, QID and description survive.
    """
    repo.upsert_entity(entity.model_copy(update={"type": new_type, "wikidata_instance_of": p31}))
