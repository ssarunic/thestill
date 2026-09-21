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

"""Stage 3 - what the chooser said, checked before anything is kept (spec #81)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .protocol import IsBlacklisted
from .types import CONFIDENCE_LEVELS, Candidate, ChoiceDecision, LinkDecision, NameGroup

NOT_OFFERED = "not_offered"
BLACKLISTED = "blacklisted"


@dataclass(frozen=True)
class Validation:
    """``decision is None`` means the answer was unusable and the name has no
    decision yet. ``rejection`` says why a QID was not kept."""

    decision: Optional[LinkDecision]
    rejection: Optional[str] = None


def meets_confidence(confidence: str, minimum: str) -> bool:
    return CONFIDENCE_LEVELS.index(confidence) >= CONFIDENCE_LEVELS.index(minimum)


class DecisionValidator:
    def __init__(self, *, is_blacklisted: Optional[IsBlacklisted] = None):
        self._is_blacklisted = is_blacklisted

    def validate(self, choice: ChoiceDecision, offered: List[Candidate], group: NameGroup) -> Validation:
        if choice.qid is None:
            return Validation(LinkDecision(group.surface_key, None, choice.confidence, choice.reason))
        chosen = next((c for c in offered if c.qid == choice.qid), None)
        if chosen is None:
            # The one rule that makes an invented identifier impossible. Not
            # a "none": the model did not answer the question it was asked.
            return Validation(None, NOT_OFFERED)
        if self.blacklisted(group.surface_form, chosen.qid):
            # A human already said this name is never this entity.
            return Validation(LinkDecision(group.surface_key, None, "high", "blacklisted by a reviewer"), BLACKLISTED)
        return Validation(LinkDecision(group.surface_key, chosen.qid, choice.confidence, choice.reason, chosen))

    def blacklisted(self, surface_form: str, qid: str) -> bool:
        return self._is_blacklisted is not None and bool(self._is_blacklisted(surface_form, qid))
