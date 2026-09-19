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
"""Alias hygiene: which stored aliases to trust, and how to remove the rest.

Background. Before the resolver fix of 2026-05-08 (#79), a mention whose
surface form matched no ReFinED span was linked to the *first* entity found
anywhere in its excerpt, and the surface was stored as that entity's alias —
``"tariffs"`` became an alias of Donald Trump. The fix stopped new ones but
repaired nothing, ``upsert_entity`` only ever unions aliases, and the anchor
extractor treats every stored alias of a host or guest as a match surface.
So each bad alias kept manufacturing new wrong mentions.

Two uses, one rule:

* **Prevention** — :func:`is_related_alias` gates which aliases the anchor
  and coref passes will match on, so a polluted alias is inert even before
  the data is cleaned.
* **Repair** — :func:`plan_alias_cleanup` decides, per stored alias, keep or
  remove; :func:`apply_alias_cleanup` rewrites the alias lists and undoes
  the mentions the removed aliases produced.

A purely lexical test is not enough for repair: ``"AWS"`` shares no text with
"Amazon Web Services", ``"Coke"`` none with "Coca-Cola". Those are told apart
from ``"tariffs"`` by *evidence*: since the fix, the resolver only links a
surface to an entity on a real span match, so an alias that still earns
direct links afterwards is one ReFinED itself vouches for.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Dict, FrozenSet, Iterable, List, Optional, Tuple

from structlog import get_logger

if TYPE_CHECKING:
    from ..repositories.entity_repository import AliasEvidence, EntityRepository
    from .queue_manager import QueueManager

logger = get_logger(__name__)

# The resolver fix (#79, d88ffda) merged 2026-05-08 16:18 UTC. Direct
# resolutions from the following day on are span-matched and count as
# independent evidence for an alias; earlier ones may be the bug itself.
RESOLVER_FIX_CUTOFF = datetime(2026, 5, 9, tzinfo=timezone.utc)

# Post-fix direct links needed to keep an alias that is not lexically
# related to its entity. Very short surfaces need more: the resolver's
# half-coverage floor is met by a single shared character for a two-letter
# surface, so "AI" still earns the odd stray link after the fix.
MIN_EVIDENCE = 1
MIN_EVIDENCE_SHORT = 3
SHORT_ALIAS_CHARS = 3
# People never keep an alias on evidence alone. Their aliases drive the
# anchor and coref passes, a real one (nickname, initials, transliteration)
# is lexically related anyway, and on the 2026-09 corpus the 18 person
# aliases evidence would have saved were nearly all wrong links ("AI" for
# Geoffrey Hinton, "Brian Armstrong" for the wrestler Road Dogg). A genuine
# exception goes in the allowlist.
EVIDENCE_EXEMPT_TYPES = frozenset({"person"})

_FUZZY_RATIO = 0.82
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS: FrozenSet[str] = frozenset({"and", "of", "the", "for", "de", "la", "in", "on", "at", "to", "a", "an"})


def _fold(text: str) -> str:
    """Casefold and strip diacritics: ``"Håland"`` -> ``"haland"``."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold().strip()


def _initials(tokens: List[str]) -> str:
    return "".join(t[0] for t in tokens if t)


def is_related_alias(alias: str, canonical_name: str) -> bool:
    """True when ``alias`` plausibly names ``canonical_name`` on its face.

    Related means any of: one contains the other, they share a token, the
    alias is the canonical's initials ("FDR", "FDA"), or they are the same
    word up to spelling ("Semmelweiss"/"Semmelweis", "Haaland"/"Håland").
    Comparison is case- and accent-insensitive, so a string that differs from
    the name only in case or accents is the name itself, not an alias of it;
    like the empty string, it returns False.
    """
    a, c = _fold(alias), _fold(canonical_name)
    if not a or not c or a == c:
        return False
    a_tokens, c_tokens = _TOKEN_RE.findall(a), _TOKEN_RE.findall(c)
    if set(a_tokens) & set(c_tokens):
        return True
    # Containment, but not for fragments: "zuck" in "zuckerberg" is a
    # nickname, "ai" in "openai" is two letters that match half the corpus.
    shorter, longer = sorted((a, c), key=len)
    if len(shorter) >= 3 and shorter in longer:
        return True
    # Handles and run-together forms: "@elonmusk", "OpenAI"/"Open AI".
    a_compact, c_compact = "".join(a_tokens), "".join(c_tokens)
    if len(a_compact) >= 4 and (a_compact in c_compact or c_compact in a_compact):
        return True
    # Initialisms: "FDA", "F.D.A." against the canonical's tokens, with and
    # without short function words ("Food *and* Drug Administration").
    if 2 <= len(a_compact) <= 6:
        content = [t for t in c_tokens if t not in _STOPWORDS]
        if a_compact in (_initials(c_tokens), _initials(content)):
            return True
    # Same word, different spelling. Compared token-to-token so a long
    # canonical cannot dilute a close match on the surname.
    for at in a_tokens:
        if len(at) < 4:
            continue
        for ct in c_tokens:
            if len(ct) >= 4 and SequenceMatcher(None, at, ct).ratio() >= _FUZZY_RATIO:
                return True
    return False


@dataclass(frozen=True)
class AliasVerdict:
    """The decision for one stored alias."""

    entity_id: str
    entity_type: str
    canonical_name: str
    alias: str
    keep: bool
    reason: str  # related | evidence | allowlisted | redundant | unsupported
    direct_since_fix: int = 0
    direct_before_fix: int = 0
    anchor_mentions: int = 0
    coref_mentions: int = 0

    @property
    def undoes_mentions(self) -> bool:
        """A redundant alias (the entity's own name) comes off the list, but
        the mentions under that surface are correct and stay."""
        return not self.keep and self.reason != "redundant"

    @property
    def mentions_to_delete(self) -> int:
        return self.anchor_mentions if self.undoes_mentions else 0

    @property
    def mentions_to_reset(self) -> int:
        if not self.undoes_mentions:
            return 0
        return self.direct_before_fix + self.direct_since_fix + self.coref_mentions


@dataclass
class AliasCleanupPlan:
    verdicts: List[AliasVerdict] = field(default_factory=list)

    @property
    def removals(self) -> List[AliasVerdict]:
        return [v for v in self.verdicts if not v.keep]

    def summary(self) -> Dict[str, int]:
        removals = self.removals
        kept_by_reason: Dict[str, int] = {}
        for v in self.verdicts:
            if v.keep:
                kept_by_reason[v.reason] = kept_by_reason.get(v.reason, 0) + 1
        return {
            "aliases_total": len(self.verdicts),
            "aliases_removed": len(removals),
            "aliases_removed_redundant": sum(v.reason == "redundant" for v in removals),
            "entities_touched": len({v.entity_id for v in removals}),
            "anchor_mentions_deleted": sum(v.mentions_to_delete for v in removals),
            "mentions_reset_to_pending": sum(v.mentions_to_reset for v in removals),
            **{f"kept_{reason}": n for reason, n in sorted(kept_by_reason.items())},
        }


def plan_alias_cleanup(
    evidence: Iterable["AliasEvidence"],
    *,
    allowlist: Optional[Iterable[Tuple[str, str]]] = None,
) -> AliasCleanupPlan:
    """Decide keep/remove for every stored alias. Pure; touches nothing.

    ``allowlist`` holds ``(entity_id, alias)`` pairs a human has vouched for;
    the alias side is matched case-insensitively.
    """
    allowed = {(entity_id, _fold(alias)) for entity_id, alias in (allowlist or ())}
    plan = AliasCleanupPlan()
    for row in evidence:
        compact = "".join(_TOKEN_RE.findall(_fold(row.alias)))
        needed = MIN_EVIDENCE_SHORT if len(compact) <= SHORT_ALIAS_CHARS else MIN_EVIDENCE
        if _fold(row.alias) == _fold(row.canonical_name):
            keep, reason = False, "redundant"
        elif is_related_alias(row.alias, row.canonical_name):
            keep, reason = True, "related"
        elif (row.entity_id, _fold(row.alias)) in allowed:
            keep, reason = True, "allowlisted"
        elif row.entity_type not in EVIDENCE_EXEMPT_TYPES and row.direct_since_fix >= needed:
            keep, reason = True, "evidence"
        else:
            keep, reason = False, "unsupported"
        plan.verdicts.append(
            AliasVerdict(
                entity_id=row.entity_id,
                entity_type=row.entity_type,
                canonical_name=row.canonical_name,
                alias=row.alias,
                keep=keep,
                reason=reason,
                direct_since_fix=row.direct_since_fix,
                direct_before_fix=row.direct_before_fix,
                anchor_mentions=row.anchor_mentions,
                coref_mentions=row.coref_mentions,
            )
        )
    return plan


@dataclass
class AliasCleanupResult:
    aliases_removed: int = 0
    entities_updated: int = 0
    anchor_mentions_deleted: int = 0
    mentions_reset: int = 0
    episodes_enqueued: int = 0
    episodes_affected: List[str] = field(default_factory=list)
    cooccurrence_pairs: Optional[int] = None


def apply_alias_cleanup(
    repo: "EntityRepository",
    plan: AliasCleanupPlan,
    *,
    queue_manager: Optional["QueueManager"] = None,
    rebuild_cooccurrences: bool = True,
) -> AliasCleanupResult:
    """Carry out ``plan``: rewrite alias lists and undo the damage.

    For each removed alias, mentions of *that entity* under *that surface*:

    * ``anchor`` mentions are deleted. They were never extracted from the
      audio as a named thing; they exist only because the alias matched.
    * ``direct`` / ``coref`` mentions are real extractions linked to the
      wrong entity, so they go back to ``pending`` for re-resolution.

    With a ``queue_manager``, one ``resolve-entities`` task is enqueued per
    affected episode. Without one (a host that cannot resolve, e.g. the prod
    image) the mentions simply stay pending — unlinked beats mislinked.

    Alias lists are rewritten last, entity by entity, so an interrupted run
    leaves aliases that a rerun will still find and finish.

    Co-occurrences are derived from mentions, so they are rebuilt in full
    afterwards: a scoped rebuild only revisits pairs still present in an
    episode and would leave the deleted pairings counted.
    """
    result = AliasCleanupResult()
    by_entity: Dict[str, List[AliasVerdict]] = {}
    for verdict in plan.removals:
        by_entity.setdefault(verdict.entity_id, []).append(verdict)

    episodes: set = set()
    for entity_id, removals in by_entity.items():
        for verdict in removals:
            if not verdict.undoes_mentions:
                continue
            result.anchor_mentions_deleted += repo.delete_mentions_by_entity_surface(
                entity_id, verdict.alias, methods=("anchor",)
            )
            affected = repo.find_mention_ids_by_entity_surface(entity_id, verdict.alias, methods=("direct", "coref"))
            if affected:
                result.mentions_reset += repo.reset_mentions_to_pending([mid for mid, _ in affected])
                episodes.update(ep for _, ep in affected)
        entity = repo.get_entity(entity_id)
        if entity is None:
            continue
        doomed = {_fold(v.alias) for v in removals}
        kept = [a for a in entity.aliases if _fold(a) not in doomed]
        if len(kept) != len(entity.aliases):
            repo.replace_aliases(entity_id, kept)
            result.entities_updated += 1
            result.aliases_removed += len(entity.aliases) - len(kept)

    result.episodes_affected = sorted(episodes)
    if queue_manager is not None:
        # Imported here so the anchor/coref passes can use is_related_alias
        # without pulling in the queue.
        from .queue_manager import TaskStage

        for episode_id in result.episodes_affected:
            queue_manager.add_task(episode_id, TaskStage.RESOLVE_ENTITIES)
        result.episodes_enqueued = len(result.episodes_affected)

    if rebuild_cooccurrences and (result.anchor_mentions_deleted or result.mentions_reset):
        result.cooccurrence_pairs = repo.rebuild_cooccurrences()

    logger.info(
        "entity_alias_cleanup_applied",
        aliases_removed=result.aliases_removed,
        entities_updated=result.entities_updated,
        anchor_mentions_deleted=result.anchor_mentions_deleted,
        mentions_reset=result.mentions_reset,
        episodes_enqueued=result.episodes_enqueued,
    )
    return result
