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

"""Spec #92 — how well the summary's Resource List grounds and links.

A supplementary check on the summary rubric: it needs the episode, the
transcript and the entity repository, which ``deterministic_checks`` does
not get, so the runner calls it separately. It never feeds ``ok`` — the
numbers are a measurement, not a gate.

- ``items``: parsed Resource List items.
- ``contract_rate``: bullets in the spec #92 contract shape.
- ``grounding_rate``: items admitted by the transcript, hosts and guests
  excluded.
- ``citation_accuracy``: admitted names said within the window of their
  citation (``near``) rather than only elsewhere.
- ``linked_rate``: admitted names that a resolved mention of this episode
  carries — only meaningful once ``resolve-entities`` has run.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from ..core.entity_anchor import expand_anchor_variants
from ..core.entity_linking.types import surface_key
from ..core.resource_seeds import ResourceSource, plan_context
from ..core.summary_resources import ResourcePlan, is_contract_line, resource_bullets
from ..models.podcast import Episode

MAX_LISTED = 10
FIND_MENTIONS_LIMIT = 5000


def resource_list_metrics(
    plan: Optional[ResourcePlan],
    *,
    bullets: int,
    contract_bullets: int,
    is_linked: Optional[Callable[[str], bool]] = None,
) -> dict:
    """Rates are ``None`` when their denominator is 0 or unknown."""
    stats = plan.stats if plan else {}
    items = stats.get("items", 0)
    anchor = stats.get("anchor", 0)
    ungrounded = stats.get("ungrounded", 0)
    near = stats.get("near", 0)
    elsewhere = stats.get("elsewhere", 0)
    names = [g.name for g in plan.grounded] if plan else []
    linked = [n for n in names if is_linked(n)] if is_linked is not None else None
    return {
        "items": items,
        "bullets": bullets,
        "contract_rate": _rate(contract_bullets, bullets),
        "grounding_rate": _rate(items - anchor - ungrounded, items - anchor),
        "citation_accuracy": _rate(near, near + elsewhere),
        "linked_rate": _rate(len(linked), len(names)) if linked is not None else None,
        "anchor_items": anchor,
        "ungrounded": [item.name for item, why in (plan.dropped if plan else ()) if why == "ungrounded"][:MAX_LISTED],
        "unlinked": [n for n in names if linked is not None and n not in linked][:MAX_LISTED],
    }


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 3) if denominator > 0 else None


class ResourceListProbe:
    """``(episode, artifact_texts) -> metrics`` for the eval runner."""

    def __init__(self, source: ResourceSource, entity_repository=None):
        self._source = source
        self._repo = entity_repository

    def __call__(self, episode: Episode, artifact_texts: Dict[str, str]) -> dict:
        markdown = artifact_texts.get("summary") or self._source.read_summary(episode) or ""
        bullets = resource_bullets(markdown)
        anchors, names, is_linked = [], [], None
        if self._repo is not None:
            anchors = [e for e in (self._repo.get_entity(i) for i in self._repo.get_episode_anchors(episode.id)) if e]
            names = self._repo.list_extracted_names(episode.id)
            is_linked = self._linked_lookup(episode.id)
        plan = self._source.plan(
            episode, plan_context(expand_anchor_variants(anchors), names), markdown=markdown, stage="eval"
        )
        return resource_list_metrics(
            plan,
            bullets=len(bullets),
            contract_bullets=sum(1 for b in bullets if is_contract_line(b)),
            is_linked=is_linked,
        )

    def _linked_lookup(self, episode_id: str) -> Callable[[str], bool]:
        keys = set()
        for row in self._repo.find_mentions(episode_id=episode_id, limit=FIND_MENTIONS_LIMIT):
            keys.add(surface_key(row.mention.surface_form))
            if row.entity_canonical_name:
                keys.add(surface_key(row.entity_canonical_name))
        return lambda name: surface_key(name) in keys
