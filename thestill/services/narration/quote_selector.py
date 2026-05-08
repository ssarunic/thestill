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

"""Deterministic quote scoring + selection (spec #33 §"Quote Selection").

The selector takes a list of resolved turns and returns the chosen
quote candidates in stable order. Determinism guarantees: given the
same turns and the same ``QuoteSelectorConfig``, ``select`` always
returns the same list, in the same order, with the same ``quote_id``
labels. This is the load-bearing property the per-run quote-id contract
in the JSON script depends on.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import QuoteCandidate
from .transcript_loader import ResolvedTurn

# Length fit — prefer 12–35 seconds of speech (~30–90 words at 150 wpm).
_TARGET_DURATION_MIN_S = 12.0
_TARGET_DURATION_MAX_S = 35.0
_TARGET_WORDS_MIN = 30
_TARGET_WORDS_MAX = 90
_HARD_MIN_WORDS = 12
_HARD_TURN_DURATION_CAP_S = 60.0
_DEFAULT_WPM = 150.0

# Diversity suppression
_NEIGHBOUR_SUPPRESS_S = 60.0
_PER_SPEAKER_MAX_PER_EPISODE = 1
_PER_EPISODE_MAX_DEFAULT = 2

# Self-containment heuristics
_LEADING_PRONOUNS = frozenset({
    "he", "she", "it", "they", "them", "him", "her", "his", "hers",
    "their", "theirs", "this", "that", "these", "those",
    "we", "us", "our",
})
_DANGLING_PHRASES = (
    "that thing", "the thing we", "as i said", "like i said",
    "what i was saying", "going back to",
)
_SENTENCE_END_RE = re.compile(r"[.!?][\"\')\]]?\s*$")


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _starts_with_pronoun(text: str) -> bool:
    stripped = text.lstrip().lstrip("\"'(").lower()
    if not stripped:
        return False
    first = re.split(r"[\s.,!?:;]", stripped, maxsplit=1)[0]
    return first in _LEADING_PRONOUNS


def _has_dangling_reference(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _DANGLING_PHRASES)


def _ends_mid_sentence(text: str) -> bool:
    return _SENTENCE_END_RE.search(text.rstrip()) is None


def truncate_to_sentence_prefix(
    text: str, max_words: int = _TARGET_WORDS_MAX
) -> Tuple[str, int]:
    """Clip ``text`` to a sentence-bounded prefix of at most ``max_words``.

    Returns ``(prefix, word_count)``. Falls back to a hard word-count
    truncation with a trailing ellipsis when no sentence boundary lies
    inside the budget — that matches spec #33's "truncated to a
    sentence-bounded prefix" intent without ever silently emitting a
    mid-sentence quote.
    """
    tokens = re.split(r"(\s+)", text)
    rebuilt: List[str] = []
    word_seen = 0
    last_sentence_end_idx: Optional[int] = None
    for tok in tokens:
        if tok and not tok.isspace():
            word_seen += 1
            rebuilt.append(tok)
            if _SENTENCE_END_RE.search(tok):
                last_sentence_end_idx = len(rebuilt)
            if word_seen >= max_words:
                break
        elif tok:
            rebuilt.append(tok)
    if last_sentence_end_idx is not None:
        prefix = "".join(rebuilt[:last_sentence_end_idx]).strip()
        return prefix, _word_count(prefix)
    prefix = "".join(rebuilt).strip()
    if not prefix.endswith(("…", "...")):
        prefix = prefix + "…"
    return prefix, _word_count(prefix)


@dataclass
class _ScoredCandidate:
    turn: ResolvedTurn
    text: str
    duration_s: float
    score: float
    word_count: int


@dataclass
class QuoteSelectorConfig:
    """Per-episode (or per-segment, in Phase 2) configuration."""

    keywords: Tuple[str, ...] = ()
    sponsors: Tuple[str, ...] = ()
    episode_duration_seconds: float = 0.0
    boundary_trim_fraction: float = 0.05
    wpm: float = _DEFAULT_WPM
    per_episode_max: int = _PER_EPISODE_MAX_DEFAULT


class QuoteSelector:
    """Deterministic quote scoring + selection.

    Phase 1 ships keyword-overlap relevance with a neutral fallback when
    no keywords are supplied; embedding-based relevance is the planned
    upgrade path (spec #33 Open Question O2). The interface is shaped so
    a future ``EmbeddingQuoteSelector`` can drop in without touching
    callers.
    """

    def select(
        self,
        turns: List[ResolvedTurn],
        config: QuoteSelectorConfig,
    ) -> List[QuoteCandidate]:
        scored = self._score_turns(turns, config)
        # Stable order: highest score wins; ties broken by earliest start
        # then by source segment_id, so reruns produce identical output.
        scored.sort(
            key=lambda c: (
                -c.score,
                c.turn.start_seconds,
                c.turn.segment_id,
            )
        )

        chosen: List[_ScoredCandidate] = []
        speaker_picks_per_episode: Dict[Tuple[str, str], int] = {}
        episode_picks: Dict[str, int] = {}
        for cand in scored:
            ep_id = cand.turn.episode_id
            speaker_key = (ep_id, cand.turn.speaker_name or "")
            if episode_picks.get(ep_id, 0) >= config.per_episode_max:
                continue
            if speaker_picks_per_episode.get(speaker_key, 0) >= _PER_SPEAKER_MAX_PER_EPISODE:
                continue
            if any(
                c.turn.episode_id == ep_id
                and abs(c.turn.start_seconds - cand.turn.start_seconds) < _NEIGHBOUR_SUPPRESS_S
                for c in chosen
            ):
                continue
            chosen.append(cand)
            episode_picks[ep_id] = episode_picks.get(ep_id, 0) + 1
            speaker_picks_per_episode[speaker_key] = (
                speaker_picks_per_episode.get(speaker_key, 0) + 1
            )

        chosen.sort(key=lambda c: (c.turn.episode_id, c.turn.start_seconds))
        results: List[QuoteCandidate] = []
        for idx, cand in enumerate(chosen, start=1):
            results.append(
                QuoteCandidate(
                    quote_id=f"q{idx}",
                    episode_id=cand.turn.episode_id,
                    podcast_title=cand.turn.podcast_title,
                    speaker=cand.turn.speaker_name or "Unknown",
                    speaker_role=cand.turn.speaker_role,
                    text=cand.text,
                    start_seconds=cand.turn.start_seconds,
                    duration_seconds=cand.duration_s,
                    score=cand.score,
                )
            )
        return results

    def _score_turns(
        self, turns: List[ResolvedTurn], config: QuoteSelectorConfig
    ) -> List[_ScoredCandidate]:
        sponsor_terms = tuple(s.lower() for s in config.sponsors if s)
        keyword_terms = tuple(k.lower() for k in config.keywords if k)
        scored: List[_ScoredCandidate] = []
        for turn in turns:
            if not turn.speaker_name:
                # spec: SPEAKER_UNKNOWN turns are not eligible as quotes.
                continue
            text = turn.text.strip()
            if not text:
                continue
            if turn.is_ad_adjacent:
                continue
            if self._is_in_boundary_trim(turn, config) and self._mentions_any(
                text, sponsor_terms
            ):
                continue
            duration_s = max(0.0, turn.end_seconds - turn.start_seconds)
            words = _word_count(text)
            if words < _HARD_MIN_WORDS:
                continue
            # Long-turn truncation: clip to a sentence-bounded prefix
            # and recompute duration at the configured WPM rate. We
            # don't have word-level timestamps yet (spec #18 / #24
            # follow-ups); the WPM estimate is the best signal we have.
            if duration_s > _HARD_TURN_DURATION_CAP_S or words > _TARGET_WORDS_MAX:
                text, words = truncate_to_sentence_prefix(text, _TARGET_WORDS_MAX)
                duration_s = (words / config.wpm) * 60.0 if config.wpm else duration_s
                if words < _TARGET_WORDS_MIN:
                    continue
            score = self._score_one(turn, text, duration_s, words, keyword_terms)
            if score < 0:
                continue
            scored.append(
                _ScoredCandidate(
                    turn=turn,
                    text=text,
                    duration_s=duration_s,
                    score=score,
                    word_count=words,
                )
            )
        return scored

    def _is_in_boundary_trim(
        self, turn: ResolvedTurn, config: QuoteSelectorConfig
    ) -> bool:
        if config.episode_duration_seconds <= 0:
            return False
        trim = config.boundary_trim_fraction * config.episode_duration_seconds
        return (
            turn.start_seconds < trim
            or turn.start_seconds > (config.episode_duration_seconds - trim)
        )

    @staticmethod
    def _mentions_any(text: str, needles: Tuple[str, ...]) -> bool:
        if not needles:
            return False
        lower = text.lower()
        return any(n in lower for n in needles)

    def _score_one(
        self,
        turn: ResolvedTurn,
        text: str,
        duration_s: float,
        words: int,
        keyword_terms: Tuple[str, ...],
    ) -> float:
        if duration_s <= 0:
            return -1.0
        # Length fit — triangle peaked between MIN..MAX with a soft
        # falloff outside the band. The falloff (vs a hard cut) keeps a
        # genuinely good 10-second line in contention rather than
        # filtering it out entirely.
        if duration_s < _TARGET_DURATION_MIN_S:
            length_score = duration_s / _TARGET_DURATION_MIN_S
        elif duration_s > _TARGET_DURATION_MAX_S:
            length_score = max(
                0.0,
                1.0 - (duration_s - _TARGET_DURATION_MAX_S) / _TARGET_DURATION_MAX_S,
            )
        else:
            length_score = 1.0

        containment_penalty = 0.0
        if _starts_with_pronoun(text):
            containment_penalty += 0.25
        if _has_dangling_reference(text):
            containment_penalty += 0.20
        if _ends_mid_sentence(text) and words <= _TARGET_WORDS_MAX:
            containment_penalty += 0.15
        containment_score = max(0.0, 1.0 - containment_penalty)

        if keyword_terms:
            lower = text.lower()
            hits = sum(1 for kw in keyword_terms if kw and kw in lower)
            relevance = min(1.0, hits / max(1, len(keyword_terms)))
        else:
            # No angle keywords (Phase 1 default) — use a neutral mid-band
            # so length and containment dominate ranking.
            relevance = 0.5

        role_bonus = 0.05 if turn.speaker_role in ("host", "guest") else 0.0
        return (
            0.45 * relevance
            + 0.35 * length_score
            + 0.20 * containment_score
            + role_bonus
        )
