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

"""Stage 4 - remembered decisions (spec #81).

The rules live here; the repository only stores rows. A name is looked up
for its podcast first, then corpus-wide. "Mercury" on a science show and
on a music show are different things, so a decision starts podcast-scoped
and is promoted only once enough podcasts agree.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from structlog import get_logger

from ...repositories.link_decision_repository import LinkDecisionRepository, StoredLinkDecision
from .types import Candidate, LinkDecision, surface_key

logger = get_logger(__name__)

PROMOTION_PODCASTS = 3
REASON_MAX_CHARS = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LinkDecisionCache:
    def __init__(
        self,
        repo: LinkDecisionRepository,
        *,
        linker_version: str,
        none_ttl_days: int = 30,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self._repo = repo
        self._linker_version = linker_version
        self._none_ttl = timedelta(days=none_ttl_days)
        self._clock = clock

    def lookup(self, key: str, podcast_id: Optional[str]) -> Optional[LinkDecision]:
        """Read-only: the eval runs the linker through this without leaving
        a trace. Reuse is counted separately, by ``record_hit``."""
        scopes = [podcast_id, None] if podcast_id else [None]
        for scope in scopes:
            row = self._repo.get(key, scope)
            if row is not None and self._usable(row):
                return LinkDecision(
                    surface_key=key,
                    qid=row.qid,
                    confidence=row.confidence,
                    reason=row.reason or "",
                    candidate=Candidate(row.qid, row.label or "", row.description or "") if row.qid else None,
                    from_cache=True,
                    cache_scope="corpus" if scope is None else "podcast",
                )
        return None

    def _usable(self, row: StoredLinkDecision) -> bool:
        # A prompt or model change re-decides names as they come up, not in bulk.
        if row.linker_version != self._linker_version:
            return False
        # An unlinked name is re-checked later: someone with no Wikidata
        # entry today may have one next month. A link does not expire.
        unlinked = row.qid is None or row.confidence == "low"
        return not (unlinked and self._clock() - row.decided_at >= self._none_ttl)

    def record_hit(self, decision: LinkDecision, podcast_id: Optional[str]) -> None:
        self._repo.record_hit(decision.surface_key, podcast_id if decision.cache_scope == "podcast" else None)

    def record(self, decision: LinkDecision, podcast_id: Optional[str]) -> None:
        """Store a fresh decision, then consider promotion. The decision is
        durable before promotion reads it back, so a crash in between leaves
        a valid podcast-scoped row (failure-mode catalogue: checkpoint
        before durability)."""
        stored = StoredLinkDecision(
            surface_key=decision.surface_key,
            podcast_id=podcast_id,
            qid=decision.qid,
            label=decision.candidate.label if decision.candidate else None,
            description=decision.candidate.description if decision.candidate else None,
            confidence=decision.confidence,
            reason=decision.reason[:REASON_MAX_CHARS] or None,
            decided_at=self._clock(),
            linker_version=self._linker_version,
        )
        self._repo.upsert(stored)
        if podcast_id is not None and decision.qid is not None and decision.confidence != "low":
            self._maybe_promote(stored)

    def _maybe_promote(self, stored: StoredLinkDecision) -> None:
        rows = [
            r
            for r in self._repo.podcast_decisions(stored.surface_key)
            if r.linker_version == self._linker_version and r.qid is not None and r.confidence != "low"
        ]
        if any(r.qid != stored.qid for r in rows):
            return  # podcasts disagree: the name stays podcast-scoped
        if len(rows) < PROMOTION_PODCASTS:
            return
        self._repo.upsert(replace(stored, podcast_id=None, reason=None))
        logger.info("entity_link_decision_promoted", qid=stored.qid, podcasts=len(rows))

    def invalidate(self, surface_form: str) -> int:
        """Forget a name in every scope. A human correction must never be
        overridden by a remembered machine answer."""
        return self._repo.delete(surface_key(surface_form))
