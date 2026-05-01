"""Within-episode coreference for spec #28 §1.13.5.

The resolver runs first and produces a mix of resolved and
unresolvable mentions per episode. This module's single function
``resolve_coreferences_for_episode`` walks the unresolved person
mentions, looks for a matching long-form anchor in the same episode,
and either repoints (single-match) or marks ambiguous (multi-match).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from structlog import get_logger

from ..models.entities import EntityMention, EntityRecord, ResolutionMethod, ResolutionStatus
from ..repositories.sqlite_entity_repository import SqliteEntityRepository

logger = get_logger(__name__)


@dataclass(frozen=True)
class CorefDecision:
    """One resolved (or marked-ambiguous) coreference outcome."""

    mention_id: int
    surface_form: str
    decided_entity_id: Optional[str]
    candidate_entity_ids: List[str]
    status: ResolutionStatus  # RESOLVED or AMBIGUOUS


def resolve_coreferences_for_episode(repository: SqliteEntityRepository, episode_id: str) -> List[CorefDecision]:
    """Apply the coref pass for one episode and persist the results.

    Returns the decisions taken — one row per unresolved person
    mention that was either repointed (RESOLVED) or marked AMBIGUOUS.
    Mentions with zero candidates remain UNRESOLVABLE and are not
    returned.
    """
    persons = repository.list_resolved_persons_for_episode(episode_id)
    if not persons:
        return []
    unresolved = repository.list_unresolved_person_mentions(episode_id)
    if not unresolved:
        return []
    decisions: List[CorefDecision] = []
    for mention in unresolved:
        candidates = _candidates_for(mention.surface_form, persons)
        if not candidates:
            continue
        if len(candidates) == 1:
            chosen = candidates[0]
            repository.resolve_mention(
                mention_id=mention.id,  # type: ignore[arg-type]
                entity_id=chosen.id,
                status="resolved",
                method=ResolutionMethod.COREF.value,
            )
            decisions.append(
                CorefDecision(
                    mention_id=mention.id,  # type: ignore[arg-type]
                    surface_form=mention.surface_form,
                    decided_entity_id=chosen.id,
                    candidate_entity_ids=[chosen.id],
                    status=ResolutionStatus.RESOLVED,
                )
            )
        else:
            ids = [c.id for c in candidates]
            repository.resolve_mention(
                mention_id=mention.id,  # type: ignore[arg-type]
                entity_id=None,
                status="ambiguous",
                method=ResolutionMethod.AMBIGUOUS.value,
                candidate_entity_ids=ids,
            )
            decisions.append(
                CorefDecision(
                    mention_id=mention.id,  # type: ignore[arg-type]
                    surface_form=mention.surface_form,
                    decided_entity_id=None,
                    candidate_entity_ids=ids,
                    status=ResolutionStatus.AMBIGUOUS,
                )
            )
    if decisions:
        logger.info(
            "coref_pass_complete",
            episode_id=episode_id,
            decisions=len(decisions),
            resolved=sum(1 for d in decisions if d.status == ResolutionStatus.RESOLVED),
            ambiguous=sum(1 for d in decisions if d.status == ResolutionStatus.AMBIGUOUS),
        )
    return decisions


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def _candidates_for(surface: str, persons: List[EntityRecord]) -> List[EntityRecord]:
    """Find resolved person entities whose canonical name (or alias)
    contains ``surface`` as a whole token.

    "Andrej" matches `person:andrej-karpathy` (canonical name "Andrej
    Karpathy") because "Andrej" is a token in the canonical name.
    "Andrej K" matches but "Karpathys" does not.
    """
    needle = surface.strip()
    if not needle:
        return []
    needle_lower = needle.lower()
    matches: List[EntityRecord] = []
    for person in persons:
        if needle_lower == person.canonical_name.lower():
            # Exact canonical match — should already have been resolved
            # by ReFinED, but skip rather than re-claim the mention.
            continue
        haystacks = [person.canonical_name] + list(person.aliases)
        for haystack in haystacks:
            tokens = {t.lower() for t in _TOKEN_RE.findall(haystack)}
            if needle_lower in tokens:
                matches.append(person)
                break
    return matches
