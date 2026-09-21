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

"""Stage 1 - candidates from live Wikidata search (spec #81)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Protocol, Set

from structlog import get_logger

from ..wikidata_client import WikidataSearchHit, WikidataUnavailable
from .rate_limiter import WikidataRateLimiter
from .types import Candidate, NameGroup

logger = get_logger(__name__)

DEFAULT_CANDIDATE_LIMIT = 8


class _EntitySearch(Protocol):
    def search_entities(self, name: str, *, language: str = "en", limit: int = 8) -> List[WikidataSearchHit]: ...


@dataclass
class CandidateFetch:
    """candidates[key] == [] is an answer ("Wikidata has nothing by that
    name"). A key in failed has no answer yet and must stay pending."""

    candidates: Dict[str, List[Candidate]] = field(default_factory=dict)
    failed: Set[str] = field(default_factory=set)
    skipped_generic: int = 0


def is_generic_noun(group: NameGroup) -> bool:
    """A single lowercase word GLiNER called a topic: "agent", "founder".

    The alias cleanup found these make up most junk mentions. The chooser
    would reject them anyway; skipping the lookup is a cost control.
    """
    name = group.surface_form.strip()
    return (group.surface_label or "").lower() == "topic" and name.isalpha() and name.islower()


class WikidataCandidateSource:
    def __init__(self, client: _EntitySearch, limiter: WikidataRateLimiter, *, limit: int = DEFAULT_CANDIDATE_LIMIT):
        self._client = client
        self._limiter = limiter
        self._limit = limit

    def fetch(self, groups: List[NameGroup], *, language: str = "en") -> CandidateFetch:
        """One search per name, in the episode's language then English.

        A failed name does not stop the others: whatever is answered can be
        decided and cached, so a retry only repeats what failed.
        """
        out = CandidateFetch()
        for group in groups:
            if is_generic_noun(group):
                out.candidates[group.surface_key] = []
                out.skipped_generic += 1
                continue
            try:
                hits = self._search(group.surface_form, language)
                if not hits and language != "en":
                    hits = self._search(group.surface_form, "en")
            except WikidataUnavailable as exc:
                if exc.retry_after_seconds:
                    self._limiter.hold_off(exc.retry_after_seconds)
                logger.warning("entity_linking_candidate_search_failed", error=str(exc))
                out.failed.add(group.surface_key)
                continue
            out.candidates[group.surface_key] = [
                Candidate(qid=h.qid, label=h.label, description=h.description) for h in hits
            ]
        return out

    def _search(self, name: str, language: str) -> List[WikidataSearchHit]:
        self._limiter.acquire()
        return self._client.search_entities(name, language=language, limit=self._limit)
