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

"""The live Wikidata linker (spec #81).

cache -> candidates from live Wikidata search -> one LLM choice among them
-> validation. Nothing here carries a snapshot of the world, so a person
who became notable last month links as soon as Wikidata has them.

``link`` is the pure core: it reads the cache and the network and writes
nothing, so an evaluation can run it side by side with another linker.
``resolve`` is the pipeline entry point: it remembers the decisions and
turns them into one result per mention.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from structlog import get_logger

from ...models.entities import EntityMention, ResolutionMethod
from .cache import LinkDecisionCache
from .candidates import WikidataCandidateSource
from .chooser import LLMCandidateChooser
from .protocol import IsBlacklisted
from .shared import (
    EntityLinkerBrokenError,
    ResolutionResult,
    _P31Lookup,
    infer_entity_type_from_label,
    linked_result,
    unresolvable_result,
)
from .types import LinkContext, LinkDecision, NameGroup, surface_key
from .validator import BLACKLISTED, NOT_OFFERED, DecisionValidator, meets_confidence

logger = get_logger(__name__)

MAX_EXCERPTS_PER_NAME = 3

# The chooser is treated as broken, rather than merely incomplete, when at
# least this many names got no usable answer AND they are half or more of
# the names it was asked about. Same rule as the ReFinED resolver.
_BROKEN_MIN_FAILURES = 3


class LinkerUnavailableError(Exception):
    """Wikidata or the LLM could not be reached for some names.

    ``results`` holds what *was* decided. Those are real decisions and safe
    to record; the names left out stay pending for the retry.
    """

    def __init__(self, message: str, *, results: List[ResolutionResult], unanswered_names: int):
        super().__init__(message)
        self.results = results
        self.unanswered_names = unanswered_names


@dataclass
class LinkOutcome:
    decisions: Dict[str, LinkDecision] = field(default_factory=dict)
    unanswered: Set[str] = field(default_factory=set)
    names: int = 0
    cache_hits: int = 0
    asked: int = 0
    omitted: int = 0  # asked, reachable, and still no answer after the re-ask
    rejected_not_offered: int = 0
    rejected_blacklisted: int = 0
    skipped_generic: int = 0
    llm_calls: int = 0
    unreachable: bool = False  # a search or an LLM call failed outright

    @property
    def unusable_answers(self) -> int:
        """Names the chooser was reachable for and still gave nothing usable:
        left out twice, or answered with a QID it was not offered."""
        return self.omitted + self.rejected_not_offered

    @property
    def broken(self) -> bool:
        return (
            not self.unreachable
            and self.unusable_answers >= _BROKEN_MIN_FAILURES
            and self.unusable_answers * 2 >= self.asked
        )


def group_mentions(mentions: List[EntityMention]) -> List[NameGroup]:
    """One group per spoken name. Version 1 assumes a name means one thing
    within an episode, so it is decided once and every mention shares it."""
    by_key: Dict[str, List[EntityMention]] = {}
    for mention in mentions:
        by_key.setdefault(surface_key(mention.surface_form), []).append(mention)
    groups = []
    for key, members in by_key.items():
        labelled = next((m for m in members if m.surface_label), members[0])
        groups.append(
            NameGroup(
                surface_key=key,
                surface_form=members[0].surface_form,
                surface_label=labelled.surface_label,
                mention_ids=[m.id for m in members],  # type: ignore[misc]  # always set when read from DB
                excerpts=_spread_excerpts(members),
            )
        )
    return groups


def _spread_excerpts(members: List[EntityMention]) -> List[str]:
    """First, middle and last: context from across the episode, not three
    sentences from the same minute."""
    excerpts = list(dict.fromkeys(m.quote_excerpt for m in members if m.quote_excerpt))
    if len(excerpts) <= MAX_EXCERPTS_PER_NAME:
        return excerpts
    return [excerpts[0], excerpts[len(excerpts) // 2], excerpts[-1]]


class LiveWikidataLinker:
    def __init__(
        self,
        *,
        candidate_source: WikidataCandidateSource,
        chooser: LLMCandidateChooser,
        cache: LinkDecisionCache,
        wikidata_client: Optional[_P31Lookup] = None,
        min_confidence: str = "medium",
    ):
        self._candidates = candidate_source
        self._chooser = chooser
        self._cache = cache
        self._wikidata_client = wikidata_client
        self._min_confidence = min_confidence

    # ------------------------------------------------------------------
    # Pure core
    # ------------------------------------------------------------------

    def link(
        self,
        groups: List[NameGroup],
        context: LinkContext,
        *,
        is_blacklisted: Optional[IsBlacklisted] = None,
    ) -> LinkOutcome:
        """Decide every name it can. Writes nothing."""
        outcome = LinkOutcome(names=len(groups))
        validator = DecisionValidator(is_blacklisted=is_blacklisted)

        misses: List[NameGroup] = []
        for group in groups:
            cached = self._cache.lookup(group.surface_key, context.podcast_id)
            if cached is not None and cached.qid and validator.blacklisted(group.surface_form, cached.qid):
                # A reviewer has since ruled this link out. Decide the name
                # again, so a correction heals the cache even if nobody
                # remembered to invalidate it.
                cached = None
            if cached is None:
                misses.append(group)
            else:
                outcome.decisions[group.surface_key] = cached
                outcome.cache_hits += 1

        started = time.monotonic()
        fetched = self._candidates.fetch(misses, language=context.language, episode_id=context.episode_id)
        outcome.skipped_generic = fetched.skipped_generic
        logger.info(
            "entity_linking_candidates_fetched",
            episode_id=context.episode_id,
            names=len(misses),
            with_candidates=sum(1 for c in fetched.candidates.values() if c),
            failed=len(fetched.failed),
            skipped_generic=fetched.skipped_generic,
            wikidata_ms=int((time.monotonic() - started) * 1000),
        )
        if fetched.failed:
            outcome.unreachable = True
            outcome.unanswered |= fetched.failed

        to_ask: List[NameGroup] = []
        for group in misses:
            if group.surface_key in fetched.failed:
                continue
            # The chooser is never offered what a reviewer ruled out, so it
            # picks among the rest instead of re-proposing it.
            offered = [
                c for c in fetched.candidates[group.surface_key] if not validator.blacklisted(group.surface_form, c.qid)
            ]
            outcome.rejected_blacklisted += len(fetched.candidates[group.surface_key]) - len(offered)
            fetched.candidates[group.surface_key] = offered
            if offered:
                to_ask.append(group)
            else:
                # Wikidata has nothing by this name: a decision, and no LLM call.
                outcome.decisions[group.surface_key] = LinkDecision(group.surface_key, None, "high", "no candidates")

        if to_ask:
            chosen = self._chooser.choose(to_ask, fetched.candidates, context)
            outcome.asked = len(to_ask)
            outcome.llm_calls = chosen.llm_calls
            outcome.unreachable = outcome.unreachable or chosen.call_errors > 0
            outcome.unanswered |= chosen.unanswered
            outcome.omitted = len(chosen.unanswered) if chosen.call_errors == 0 else 0
            for group in to_ask:
                choice = chosen.decisions.get(group.surface_key)
                if choice is None:
                    continue
                checked = validator.validate(choice, fetched.candidates[group.surface_key], group)
                if checked.rejection == NOT_OFFERED:
                    outcome.rejected_not_offered += 1
                    outcome.unanswered.add(group.surface_key)
                    continue
                if checked.rejection == BLACKLISTED:
                    outcome.rejected_blacklisted += 1
                outcome.decisions[group.surface_key] = checked.decision  # type: ignore[assignment]
            fresh = [outcome.decisions[g.surface_key] for g in to_ask if g.surface_key in outcome.decisions]
            logger.info(
                "entity_linking_chosen",
                episode_id=context.episode_id,
                asked=outcome.asked,
                llm_calls=outcome.llm_calls,
                linked=sum(1 for d in fresh if d.qid and d.confidence != "low"),
                none=sum(1 for d in fresh if d.qid is None),
                low_confidence=sum(1 for d in fresh if d.qid and d.confidence == "low"),
                omitted=outcome.omitted,
                # above zero means the model is inventing QIDs: look at the prompt
                rejected_not_offered=outcome.rejected_not_offered,
                rejected_blacklisted=outcome.rejected_blacklisted,
            )
        return outcome

    # ------------------------------------------------------------------
    # Pipeline entry point
    # ------------------------------------------------------------------

    def resolve(
        self,
        mentions: List[EntityMention],
        *,
        is_blacklisted: Optional[IsBlacklisted] = None,
        context: Optional[LinkContext] = None,
    ) -> List[ResolutionResult]:
        if not mentions:
            return []
        started = time.monotonic()
        context = context or LinkContext(episode_id=mentions[0].episode_id)
        groups = group_mentions(mentions)
        logger.info("entity_linking_started", episode_id=context.episode_id, names=len(groups), mentions=len(mentions))

        outcome = self.link(groups, context, is_blacklisted=is_blacklisted)

        if outcome.broken:
            # Most of what came back was unusable, so none of it is trusted:
            # nothing is remembered and nothing is recorded.
            raise EntityLinkerBrokenError(
                f"the chooser gave no usable answer for {outcome.unusable_answers} of {outcome.asked} names "
                f"({outcome.rejected_not_offered} with a QID it was not offered); refusing to record this batch"
            )

        for key, decision in outcome.decisions.items():
            if decision.from_cache:
                self._cache.record_hit(decision, context.podcast_id)
            else:
                self._cache.record(decision, context.podcast_id)

        by_id = {m.id: m for m in mentions}
        results: List[ResolutionResult] = []
        linked_names = 0
        for group in groups:
            decision = outcome.decisions.get(group.surface_key)
            if decision is None:
                continue  # no answer: these mentions stay pending
            accepted = self._accepted(decision, group, is_blacklisted)
            linked_names += 1 if accepted else 0
            for mention_id in group.mention_ids:
                mention = by_id[mention_id]
                results.append(self._linked_result(mention, decision) if accepted else unresolvable_result(mention))

        logger.info(
            "entity_linking_completed",
            episode_id=context.episode_id,
            names=outcome.names,
            cache_hits=outcome.cache_hits,
            skipped_generic=outcome.skipped_generic,
            asked=outcome.asked,
            llm_calls=outcome.llm_calls,
            linked_names=linked_names,
            unanswered_names=len(outcome.unanswered),
            rejected_not_offered=outcome.rejected_not_offered,
            rejected_blacklisted=outcome.rejected_blacklisted,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

        if outcome.unanswered and outcome.unreachable:
            raise LinkerUnavailableError(
                f"no answer for {len(outcome.unanswered)} of {outcome.names} names: Wikidata or the LLM was unreachable",
                results=results,
                unanswered_names=len(outcome.unanswered),
            )
        return results

    def _accepted(self, decision: LinkDecision, group: NameGroup, is_blacklisted: Optional[IsBlacklisted]) -> bool:
        if decision.qid is None or not meets_confidence(decision.confidence, self._min_confidence):
            return False
        # ``link`` already re-decides a remembered link that was ruled out
        # since; this is the last line of defence before a mention is written.
        return not (is_blacklisted is not None and is_blacklisted(group.surface_form, decision.qid))

    def _linked_result(self, mention: EntityMention, decision: LinkDecision) -> ResolutionResult:
        assert decision.qid is not None
        candidate = decision.candidate
        return linked_result(
            mention,
            qid=decision.qid,
            canonical_name=(candidate.label if candidate else "") or mention.surface_form,
            description=(candidate.description if candidate else None) or None,
            fallback_type=infer_entity_type_from_label(mention),
            method=ResolutionMethod.LLM_LINKED,
            wikidata_client=self._wikidata_client,
        )
