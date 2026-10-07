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

"""Re-decide links the retired ReFinED linker made (spec #81 Phase 4).

ReFinED links (``resolution_method='direct'``, before the 2026-09-23
cutover to the live linker) include junk: "healthcare" on Love, "Opus" and
"Pope" on Anthropic principle, "ChatGPT" on First officer. Relinking resets
such mentions to ``pending`` and enqueues one ``resolve-entities`` task per
episode, so the live linker decides each again with context. Nothing is
deleted: a link that was right ("Twitter" on X) comes back to the same
entity, a wrong one moves or becomes unlinked.

``unrelated_only`` (the default) picks only mentions whose surface does not
resemble the entity's name — the same ``is_related_alias`` rule the alias
cleanup uses. Without it, every ReFinED link in scope is re-decided: the
full Phase 4 sweep, run in episode-capped batches. A relinked mention stops
being ``direct``, so each batch naturally moves on to the next.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from .entity_alias_hygiene import is_related_alias

# (mention_id, episode_id, surface_form, entity_id, canonical_name), newest episode first.
RelinkRow = Tuple[int, str, str, str, str]

RESET_BATCH_SIZE = 1000


@dataclass
class RelinkPlan:
    mention_ids: List[int] = field(default_factory=list)
    episode_ids: List[str] = field(default_factory=list)  # in the order rows arrived
    scanned: int = 0
    skipped_related: int = 0
    by_entity: Counter = field(default_factory=Counter)  # canonical name -> mentions selected


def is_unrelated_surface(surface: str, canonical_name: str) -> bool:
    """True when the spoken text does not visibly name the entity."""
    if not surface:
        return False
    if surface.casefold() == canonical_name.casefold():
        return False
    return not is_related_alias(surface, canonical_name)


def plan_relink(
    rows: Iterable[RelinkRow],
    *,
    unrelated_only: bool = True,
    max_episodes: Optional[int] = None,
) -> RelinkPlan:
    """Pick the mentions to re-decide.

    ``rows`` arrive newest episode first; ``max_episodes`` keeps the
    mentions of the first N episodes that have anything to relink, so a
    batch is a set of whole episodes, never half of one.
    """
    plan = RelinkPlan()
    episodes: dict = {}
    for mention_id, episode_id, surface, _entity_id, canonical_name in rows:
        plan.scanned += 1
        if unrelated_only and not is_unrelated_surface(surface, canonical_name):
            plan.skipped_related += 1
            continue
        if episode_id not in episodes:
            if max_episodes is not None and len(episodes) >= max_episodes:
                continue
            episodes[episode_id] = None
        plan.mention_ids.append(mention_id)
        plan.by_entity[canonical_name] += 1
    plan.episode_ids = list(episodes)
    return plan


def apply_relink(repo, queue_manager, plan: RelinkPlan) -> int:
    """Reset the planned mentions to pending, then enqueue their episodes.

    Mentions are reset before anything is enqueued, so a task never runs on
    an episode whose mentions are not yet pending. Returns rows reset.
    """
    from .queue_manager import TaskStage

    reset = 0
    for start in range(0, len(plan.mention_ids), RESET_BATCH_SIZE):
        reset += repo.reset_mentions_to_pending(plan.mention_ids[start : start + RESET_BATCH_SIZE])
    for episode_id in plan.episode_ids:
        queue_manager.add_task(episode_id, TaskStage.RESOLVE_ENTITIES)
    return reset
