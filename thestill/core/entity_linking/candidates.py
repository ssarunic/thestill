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
from typing import Dict, List, Optional, Protocol, Set

from structlog import get_logger

from ...utils.text_sanitizer import sanitize_text
from ..wikidata_client import WikidataSearchHit, WikidataUnavailable
from .rate_limiter import WikidataRateLimiter
from .types import Candidate, NameGroup

logger = get_logger(__name__)

DEFAULT_CANDIDATE_LIMIT = 8
WIKIPEDIA_LIMIT = 5


class _EntitySearch(Protocol):
    def search_entities(self, name: str, *, language: str = "en", limit: int = 8) -> List[WikidataSearchHit]: ...


@dataclass
class CandidateFetch:
    """candidates[key] == [] is an answer ("Wikidata has nothing by that
    name"). A key in failed has no answer yet and must stay pending."""

    candidates: Dict[str, List[Candidate]] = field(default_factory=dict)
    failed: Set[str] = field(default_factory=set)
    skipped_generic: int = 0


# Words the extractor tags as entities that never are one. Taken from what
# the 2026-09 alias cleanup removed ("agent", "people", "founder", "ceo"...).
# A closed list on purpose: "any lowercase word tagged topic" would also
# swallow "bitcoin", "ozempic" and "kubernetes", and a skipped name is
# remembered as "no such entity" - the failure this linker exists to fix.
GENERIC_NOUNS = frozenset(
    """
    agent agents app apps boss book business ceo cfo chairman city companies company corporation country
    cto customer customers data director doctor economy employee employees episode film founder founders
    government guest guy guys host idea ideas internet investor investors man manager market markets model
    models money movie patient patients people person podcast policy politics president price prices
    product products show startup team tech technology thinking user users woman world
    """.split()
)


def is_generic_noun(group: NameGroup) -> bool:
    """The chooser would reject these anyway; skipping the lookup is a cost
    control, never a quality rule."""
    return group.surface_key in GENERIC_NOUNS


class WikidataCandidateSource:
    """Candidates from Wikidata's label search plus, when given, Wikipedia's
    relevance search. The first is exact on labels and aliases; the second
    finds "Barack Obama" for "Obama" and "Michael Moritz" for "Mike Moritz"
    through redirects and text. Both answers merge, Wikipedia's first,
    without duplicates."""

    def __init__(
        self,
        client: _EntitySearch,
        limiter: WikidataRateLimiter,
        *,
        wikipedia: Optional[_EntitySearch] = None,
        limit: int = DEFAULT_CANDIDATE_LIMIT,
    ):
        self._client = client
        self._wikipedia = wikipedia
        self._limiter = limiter
        self._limit = limit

    def fetch(self, groups: List[NameGroup], *, language: str = "en", episode_id: str = "") -> CandidateFetch:
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
                # The name is spoken content and stays out of the log; the
                # error text carries only a status code or an exception type.
                logger.warning("entity_linking_candidate_search_failed", episode_id=episode_id, error=str(exc))
                out.failed.add(group.surface_key)
                continue
            out.candidates[group.surface_key] = [
                Candidate(qid=h.qid, label=_clean(h.label), description=_clean(h.description)) for h in hits
            ]
        return out

    def _search(self, name: str, language: str) -> List[WikidataSearchHit]:
        merged: List[WikidataSearchHit] = []
        if self._wikipedia is not None:
            self._limiter.acquire()
            merged += self._wikipedia.search_entities(name, language=language, limit=WIKIPEDIA_LIMIT)
        self._limiter.acquire()
        merged += self._client.search_entities(name, language=language, limit=self._limit)
        seen: set = set()
        unique = [h for h in merged if not (h.qid in seen or seen.add(h.qid))]
        return unique[: self._limit + WIKIPEDIA_LIMIT]


def _clean(text: str) -> str:
    """Wikidata is publicly editable, and its labels end up as entity names.
    Scrub control characters here, and say so (spec #42 FM-4)."""
    clean, removed = sanitize_text(text or "")
    if removed:
        logger.warning("wikidata_control_chars_stripped", removed=removed)
    return clean.strip()
