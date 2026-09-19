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

"""One claim and one piece of colour per episode (spec #77 Phase 2b).

Handing the writer every takeaway and every drama round produced a list
read back as a list. This module picks, deterministically, the single
takeaway that best serves the segment angle and the drama round closest
to that claim, so "one idea per show" holds by construction. Token
overlap is the v1 scorer; the interface is shaped so an embedding scorer
can drop in later (spec #33 O2).
"""

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Set

from .models import EpisodeBrief, word_count

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS: Set[str] = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "vs",
    "what",
    "when",
    "who",
    "why",
    "with",
    "about",
    "into",
    "than",
    "they",
    "them",
    "he",
    "she",
    "his",
    "her",
}


@dataclass(frozen=True)
class EpisodeClaim:
    claim: str
    colour: Optional[str] = None


def _tokens(text: str) -> Set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


def _best(candidates: Sequence[str], against: str) -> Optional[str]:
    """Highest token overlap with ``against``; first candidate on ties or no overlap."""
    if not candidates:
        return None
    target = _tokens(against)
    best, best_score = candidates[0], -1
    for cand in candidates:
        score = len(_tokens(cand) & target)
        if score > best_score:
            best, best_score = cand, score
    return best


# Content tokens a quote must share with the claim (plus colour) before
# the writer is told it illustrates that claim.
_FIT_MIN_OVERLAP = 2


def quote_fits_claim(quote_text: str, claim_text: Optional[str]) -> bool:
    """True when ``quote_text`` shares enough content tokens with the claim.

    A tail episode (no claim) never fits; the writer cues those clips on
    the gist alone, as before.
    """
    if not claim_text:
        return False
    return len(_tokens(quote_text) & _tokens(claim_text)) >= _FIT_MIN_OVERLAP


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0] if parts and parts[0] else text.strip()


def _cap(text: str, max_words: int) -> str:
    """Trim to whole sentences under ``max_words``; at least one sentence."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: list[str] = []
    used = 0
    for sentence in sentences:
        n = word_count(sentence)
        if kept and used + n > max_words:
            break
        kept.append(sentence)
        used += n
    return " ".join(kept)


def select_claim(brief: EpisodeBrief, angle: str, *, max_words: int = 400) -> Optional[EpisodeClaim]:
    """Pick the claim (and colour) the writer gets for ``brief`` in a segment.

    - claim: the Key Takeaway with the most token overlap with ``angle``;
      the first takeaway on ties. Falls back to the first gist sentence.
    - colour: the Drama round closest to the chosen claim, capped so
      claim + colour stay within ``max_words``. ``None`` without drama.
    """
    claim = _best(brief.takeaways, angle)
    if claim is None:
        if not brief.gist:
            return None
        claim = _first_sentence(brief.gist)
    colour = _best(brief.drama, claim)
    if colour is not None:
        budget = max(1, max_words - word_count(claim))
        colour = _cap(colour, budget)
    return EpisodeClaim(claim=claim, colour=colour)
