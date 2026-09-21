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

"""Values passed between the linking stages (spec #81).

Every stage takes and returns these, so each can be built and tested
alone: names in, candidates out; candidates in, choices out; choices in,
validated decisions out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

CONFIDENCE_LEVELS = ("low", "medium", "high")


def surface_key(name: str) -> str:
    """The cache and grouping key for a spoken name.

    Folded in Python, never in SQL: SQLite's LOWER is ASCII-only and
    Postgres's follows the collation, so the two would disagree on "Škoda".
    """
    return " ".join(name.split()).casefold()


@dataclass(frozen=True)
class LinkContext:
    """What the chooser knows about the episode beyond the excerpts."""

    episode_id: str
    podcast_id: Optional[str] = None
    language: str = "en"
    podcast_title: str = ""
    episode_title: str = ""
    anchor_names: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class NameGroup:
    """Every mention of one name in one episode. Resolved once, together."""

    surface_key: str
    surface_form: str
    surface_label: Optional[str]
    mention_ids: List[int]
    excerpts: List[str]


@dataclass(frozen=True)
class Candidate:
    qid: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class ChoiceDecision:
    """What the chooser said, before validation."""

    surface_key: str
    qid: Optional[str]
    confidence: str
    reason: str = ""


@dataclass(frozen=True)
class LinkDecision:
    """A validated answer for one name: a QID, or a decided "none".

    candidate carries the chosen candidate's label and description so
    the caller can build the entity without a second lookup. from_cache
    decisions are not written back.
    """

    surface_key: str
    qid: Optional[str]
    confidence: str
    reason: str = ""
    candidate: Optional[Candidate] = None
    from_cache: bool = False
