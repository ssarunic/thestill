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

"""The contract resolve-entities depends on (spec #81)."""

from __future__ import annotations

from typing import Callable, List, Optional, Protocol

from ...models.entities import EntityMention
from .shared import ResolutionResult
from .types import LinkContext

IsBlacklisted = Callable[[str, str], bool]


class EntityLinker(Protocol):
    """Anything that can resolve an episode's pending mentions.

    One ResolutionResult per mention, in any order. unresolvable
    is a decision; a failure raises. context is optional so a linker
    that works from the excerpt alone can ignore it; such a linker sets
    ``uses_context = False`` and callers skip building it (it costs one
    repository read per anchor).
    """

    uses_context: bool

    def resolve(
        self,
        mentions: List[EntityMention],
        *,
        is_blacklisted: Optional[IsBlacklisted] = None,
        context: Optional[LinkContext] = None,
    ) -> List[ResolutionResult]: ...
