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
"""Undo anchor-scan mentions that re-read part of a name GLiNER had found.

Background. The anchor scan (spec #28 §1.13.4) looks for host and guest names
GLiNER missed ("Cubuk" alone, "Gary" alone). Until 2026-09-30 it skipped a
GLiNER span only on an exact match of its offsets, so the shorter variants
still matched *inside* it: "Liam Fedus and Dogus Cubuk" gave three rows for
one name (GLiNER's "Dogus Cubuk" plus the scan's "Dogus" and "Cubuk"), and
"Gary Neville" also produced a "Gary" credited to the host Gary Lineker. The
extractor is fixed; this removes the rows it already wrote.

Offsets are not stored, so each scan row is judged from its quote excerpt
(the text around its own match). The scan's name is nested when every
whole-word occurrence of it in the quote sits inside the longer name. Where
the same scan name also appears on its own in the segment, only the surplus
rows go: one scan row per free occurrence stays, and a row whose quote shows
a free occurrence is never the one deleted. Anything undecidable is kept.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from structlog import get_logger

if TYPE_CHECKING:
    from ..repositories.entity_repository import AnchorScanOverlap, EntityRepository

logger = get_logger(__name__)


def _word(surface: str) -> "re.Pattern[str]":
    return re.compile(r"(?<!\w)" + re.escape(surface) + r"(?!\w)", re.IGNORECASE)


def free_occurrences(surface: str, longer: List[str], quote: str) -> Tuple[int, int]:
    """``(total, free)`` whole-word occurrences of ``surface`` in ``quote``;
    free ones lie outside every occurrence of the ``longer`` names."""
    covered: List[Tuple[int, int]] = []
    for name in longer:
        covered.extend(m.span() for m in _word(name).finditer(quote))
    total = free = 0
    for m in _word(surface).finditer(quote):
        total += 1
        if not any(start <= m.start() and m.end() <= end for start, end in covered):
            free += 1
    return total, free


@dataclass(frozen=True)
class NestedMention:
    """An ``anchor:scan`` row to delete, with the name it was read out of."""

    mention_id: int
    episode_id: str
    entity_id: str
    surface_form: str
    inside: str
    inside_entity_id: str
    quote_excerpt: str

    @property
    def misattributed(self) -> bool:
        """The longer name belongs to someone else ("Gary" in "Gary Neville")."""
        return self.entity_id != self.inside_entity_id


@dataclass
class AnchorScanRepairPlan:
    deletions: List[NestedMention] = field(default_factory=list)
    # Scan rows beside a longer name that were kept because they (or the
    # rows they can't be told apart from) name the entity on their own.
    kept_ambiguous: int = 0

    @property
    def episodes(self) -> List[str]:
        return sorted({d.episode_id for d in self.deletions})

    def summary(self) -> Dict[str, int]:
        misattributed = sum(d.misattributed for d in self.deletions)
        return {
            "rows to delete": len(self.deletions),
            "  same entity counted again": len(self.deletions) - misattributed,
            "  credited to the wrong entity": misattributed,
            "episodes affected": len(self.episodes),
            "kept (ambiguous)": self.kept_ambiguous,
        }


def plan_anchor_scan_repair(overlaps: List["AnchorScanOverlap"]) -> AnchorScanRepairPlan:
    """Decide which scan rows were read out of a longer name."""
    rows: Dict[int, "AnchorScanOverlap"] = {}
    longer_names: Dict[int, List[Tuple[str, str]]] = defaultdict(list)
    for o in overlaps:
        if _word(o.surface_form).search(o.other_surface_form):
            rows[o.mention_id] = o
            longer_names[o.mention_id].append((o.other_surface_form, o.other_entity_id))

    # Rows for one name in one segment can't be told apart (same segment,
    # often the same quote): judge them together.
    groups: Dict[Tuple[str, int, str], List[int]] = defaultdict(list)
    for mention_id, o in rows.items():
        groups[(o.episode_id, o.segment_id, o.surface_form.lower())].append(mention_id)

    plan = AnchorScanRepairPlan()
    for ids in groups.values():
        nested: List[int] = []
        free_by_quote: Dict[str, int] = {}
        for mention_id in sorted(ids):
            o = rows[mention_id]
            longer = [name for name, _ in longer_names[mention_id]]
            total, free = free_occurrences(o.surface_form, longer, o.quote_excerpt)
            free_by_quote[o.quote_excerpt] = free
            if total and not free:
                nested.append(mention_id)
        # One scan row per free occurrence is a real mention; distinct quotes
        # may show the same occurrence twice, which only keeps more rows.
        surplus = len(ids) - sum(free_by_quote.values())
        doomed = nested[: max(0, surplus)]
        plan.kept_ambiguous += len(ids) - len(doomed)
        for mention_id in doomed:
            o = rows[mention_id]
            inside, inside_entity = _containing_name(o.entity_id, longer_names[mention_id], o.quote_excerpt)
            plan.deletions.append(
                NestedMention(
                    mention_id=mention_id,
                    episode_id=o.episode_id,
                    entity_id=o.entity_id,
                    surface_form=o.surface_form,
                    inside=inside,
                    inside_entity_id=inside_entity,
                    quote_excerpt=o.quote_excerpt,
                )
            )
    return plan


def _containing_name(entity_id: str, candidates: List[Tuple[str, str]], quote: str) -> Tuple[str, str]:
    """The longer name the scan row was read out of: one visible in its quote,
    the entity's own if there is one (so a duplicate isn't reported as a
    misattribution), longest first."""
    visible = [c for c in candidates if _word(c[0]).search(quote)] or candidates
    return min(visible, key=lambda c: (c[1] != entity_id, -len(c[0])))


@dataclass
class AnchorScanRepairResult:
    deleted: int
    episodes: List[str]
    cooccurrence_pairs: Optional[int]


def apply_anchor_scan_repair(repo: "EntityRepository", plan: AnchorScanRepairPlan) -> AnchorScanRepairResult:
    """Delete the planned rows and recount co-occurrences for their episodes
    (a misattributed row may have been the entity's only one there)."""
    deleted = repo.delete_mentions_by_ids([d.mention_id for d in plan.deletions])
    episodes = plan.episodes
    pairs = repo.rebuild_cooccurrences(episode_ids=episodes) if episodes else None
    logger.info("anchor_scan_repair_applied", deleted=deleted, episodes=len(episodes), cooccurrence_pairs=pairs)
    return AnchorScanRepairResult(deleted=deleted, episodes=episodes, cooccurrence_pairs=pairs)
