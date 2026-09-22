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

"""Abstract repository for the live linker's decision cache (spec #81).

Storage only. Which scope to read first, when a "none" has expired and
when a name is promoted corpus-wide are the cache's rules
(``core/entity_linking/cache.py``), not the table's. ``surface_key``
arrives already case-folded - never fold it in SQL.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class StoredLinkDecision:
    surface_key: str
    podcast_id: Optional[str]  # None = corpus-wide
    qid: Optional[str]  # None = decided "none"
    label: Optional[str]  # the entity's Wikidata label and description, so a
    description: Optional[str]  # cache hit builds the entity without a lookup
    confidence: str
    reason: Optional[str]
    decided_at: datetime
    linker_version: str
    hits: int = 0


class LinkDecisionRepository(ABC):
    """Abstract repository for ``entity_link_decisions``."""

    @abstractmethod
    def get(self, surface_key: str, podcast_id: Optional[str]) -> Optional[StoredLinkDecision]:
        """The row for exactly this scope (``None`` = corpus-wide), or None."""

    @abstractmethod
    def upsert(self, decision: StoredLinkDecision) -> None:
        """Create or replace the row for the decision's scope in one
        statement. A replaced row keeps its ``hits``. Two episodes deciding
        the same name at once both succeed; the later write stands."""

    @abstractmethod
    def record_hit(self, surface_key: str, podcast_id: Optional[str]) -> None:
        """Count one reuse of the row for this scope."""

    @abstractmethod
    def podcast_decisions(self, surface_key: str) -> List[StoredLinkDecision]:
        """Every podcast-scoped row of this name (never the corpus-wide one)."""

    @abstractmethod
    def delete(self, surface_key: str) -> int:
        """Remove the name's rows in every scope; returns how many."""
