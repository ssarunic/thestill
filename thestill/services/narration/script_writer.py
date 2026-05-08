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

"""Anchor-prose script-generation LLM call (spec #33 Pipeline Stage 4).

Takes the theme plan + the verbatim quote pool + episode summaries and
asks the model to weave the day's episodes into a coherent readout.
The script-generation contract is load-bearing — the model must not
paraphrase a quoted line, must not invent quote ids, and must stay
inside the narration word budget. We validate every output and
regenerate once with a tightened prompt before falling back to the
link-index digest.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, Field
from structlog import get_logger

from ...core.llm_provider import LLMProvider
from .models import (
    EpisodeBrief,
    QuoteCandidate,
    ScriptBlock,
    ScriptBlockKind,
    Segment,
    ThemePlan,
    ValidationFailure,
    word_count,
)

logger = get_logger(__name__)


# Validation tolerances
WORD_BUDGET_TOLERANCE = 0.15  # ±15%
VERBATIM_LEAK_NGRAM = 8  # an 8-word verbatim slice from a quote in narration → leak
MAX_REGENERATIONS = 1  # one retry, then fall back


class _ScriptBlockOut(BaseModel):
    kind: ScriptBlockKind
    section: str = Field(..., min_length=1, max_length=64)
    text: Optional[str] = None
    quote_id: Optional[str] = None


class _ScriptOut(BaseModel):
    blocks: List[_ScriptBlockOut] = Field(default_factory=list)


@dataclass(frozen=True)
class ScriptResult:
    """Outcome of a single script-generation attempt.

    ``blocks`` is non-empty exactly when ``failures`` is empty.
    """

    blocks: Tuple[ScriptBlock, ...]
    failures: Tuple[ValidationFailure, ...]
    raw_word_count: int


class ScriptWriter:
    """Generate the anchor-voiced script with built-in validation + retry.

    ``write(...)`` returns the validated script blocks (with
    ``failures=()``) on success, or the latest failures alongside an
    empty block list when both the initial call and the regeneration
    fail. The generator interprets the empty-blocks result as the
    fallback signal.
    """

    def __init__(self, provider: LLMProvider, system_prompt: str, wpm: float = 150.0):
        self.provider = provider
        self.system_prompt = system_prompt
        self.wpm = wpm

    def write(
        self,
        plan: ThemePlan,
        briefs_by_id: Mapping[str, EpisodeBrief],
        quotes: Sequence[QuoteCandidate],
        narration_word_budget: int,
    ) -> ScriptResult:
        if narration_word_budget <= 0:
            return ScriptResult(
                blocks=(),
                failures=(
                    ValidationFailure(
                        reason="empty_blocks",
                        detail="narration_word_budget must be positive",
                    ),
                ),
                raw_word_count=0,
            )

        quotes_by_id = {q.quote_id: q for q in quotes}
        user_prompt = self._build_user_prompt(plan, briefs_by_id, quotes, narration_word_budget)
        attempts: List[Tuple[Tuple[ScriptBlock, ...], Tuple[ValidationFailure, ...], int]] = []
        for attempt in range(MAX_REGENERATIONS + 1):
            try:
                result = self.provider.generate_structured(
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=_ScriptOut,
                    temperature=0.4 if attempt == 0 else 0.2,
                )
            except Exception as exc:  # noqa: BLE001 — return as a fallback signal
                logger.warning(
                    "narration: script generation LLM call failed",
                    attempt=attempt,
                    error=str(exc),
                )
                attempts.append(
                    (
                        (),
                        (
                            ValidationFailure(
                                reason="llm_error",
                                detail=str(exc),
                            ),
                        ),
                        0,
                    )
                )
                break

            blocks = self._normalise_blocks(result.blocks, quotes_by_id, self.wpm)
            failures = self._validate(blocks, quotes_by_id, narration_word_budget)
            raw_words = sum(
                word_count(b.text) for b in blocks if b.kind == "narration" and b.text
            )
            if not failures:
                return ScriptResult(blocks=tuple(blocks), failures=(), raw_word_count=raw_words)

            logger.info(
                "narration: script validation failed",
                attempt=attempt,
                failures=[f.reason for f in failures],
            )
            attempts.append((tuple(blocks), tuple(failures), raw_words))
            if attempt < MAX_REGENERATIONS:
                user_prompt = self._tighten_prompt(user_prompt, failures, narration_word_budget)

        # Both attempts failed (or LLM error path). Surface the latest
        # failure list to the caller — the generator will trigger the
        # link-index fallback and log a ``narration.fallback`` event.
        last = attempts[-1] if attempts else ((), (), 0)
        return ScriptResult(blocks=(), failures=last[1], raw_word_count=last[2])

    def _build_user_prompt(
        self,
        plan: ThemePlan,
        briefs_by_id: Mapping[str, EpisodeBrief],
        quotes: Sequence[QuoteCandidate],
        narration_word_budget: int,
    ) -> str:
        parts: List[str] = [
            f"Narration word budget: {narration_word_budget} words"
            f" (±{int(WORD_BUDGET_TOLERANCE * 100)}% tolerance, counts only narration"
            " block text — quote blocks are excluded).",
            "",
            "Quote pool (verbatim — do not retype these in your narration):",
        ]
        if quotes:
            for q in quotes:
                parts.append(
                    f"- quote_id={q.quote_id} | speaker={q.speaker} ({q.speaker_role})"
                    f" | podcast={q.podcast_title} | episode_id={q.episode_id}"
                )
                parts.append(f"  text: {q.text}")
        else:
            parts.append("- (none — produce opener, tail, and signoff only)")
        parts.append("")

        if plan.segments:
            parts.append("Segment plan:")
            for seg in plan.segments:
                parts.append(self._format_segment(seg, briefs_by_id))
        else:
            parts.append(
                "Segment plan: (empty — emit opener, tail-only narration, signoff)"
            )

        if plan.tail_ids:
            parts.append("")
            parts.append("Tail bucket (rapid-fire mentions):")
            for eid in plan.tail_ids:
                brief = briefs_by_id.get(eid)
                if brief is None:
                    continue
                parts.append(
                    f"- episode_id={eid} | podcast={brief.podcast_title}"
                    f" | title={brief.episode_title}"
                )

        return "\n".join(parts)

    @staticmethod
    def _format_segment(
        seg: Segment, briefs_by_id: Mapping[str, EpisodeBrief]
    ) -> str:
        lines = [
            "",
            f"Segment {seg.rank}: {seg.theme}",
            f"  angle: {seg.angle}",
            f"  section: segment-{seg.rank}",
        ]
        for eid in seg.episode_ids:
            brief = briefs_by_id.get(eid)
            if brief is None:
                continue
            lines.append(
                f"  - episode_id={eid} | podcast={brief.podcast_title}"
                f" | title={brief.episode_title}"
            )
            if brief.guests:
                lines.append(f"    guests: {', '.join(brief.guests)}")
            if brief.gist:
                lines.append(f"    gist: {brief.gist}")
        return "\n".join(lines)

    @staticmethod
    def _normalise_blocks(
        out_blocks: Sequence[_ScriptBlockOut],
        quotes_by_id: Mapping[str, QuoteCandidate],
        wpm: float,
    ) -> List[ScriptBlock]:
        blocks: List[ScriptBlock] = []
        for raw in out_blocks:
            if raw.kind == "narration":
                text = (raw.text or "").strip() or None
                duration = (
                    word_count(text) / wpm * 60.0 if text and wpm else 0.0
                )
                blocks.append(
                    ScriptBlock(
                        kind="narration",
                        section=raw.section,
                        text=text,
                        duration_seconds=duration,
                    )
                )
            else:
                quote = quotes_by_id.get(raw.quote_id or "")
                blocks.append(
                    ScriptBlock(
                        kind="quote",
                        section=raw.section,
                        quote_id=raw.quote_id,
                        duration_seconds=quote.duration_seconds if quote else 0.0,
                    )
                )
        return blocks

    def _validate(
        self,
        blocks: Sequence[ScriptBlock],
        quotes_by_id: Mapping[str, QuoteCandidate],
        narration_word_budget: int,
    ) -> Tuple[ValidationFailure, ...]:
        failures: List[ValidationFailure] = []
        if not blocks:
            failures.append(
                ValidationFailure(
                    reason="empty_blocks",
                    detail="model returned no blocks",
                )
            )
            return tuple(failures)

        for idx, b in enumerate(blocks):
            if b.kind != "quote":
                continue
            if not b.quote_id or b.quote_id not in quotes_by_id:
                failures.append(
                    ValidationFailure(
                        reason="unknown_quote_id",
                        detail=(
                            f"block {idx} (kind=quote) references unknown quote_id="
                            f"{b.quote_id!r}"
                        ),
                    )
                )

        # An 8-word slice that appears verbatim in any quote is the
        # paraphrase-leak signal — the model copied a quote into the
        # narration instead of cueing it. Spec #33 §"Script Generation".
        narration_text = " ".join(
            b.text for b in blocks if b.kind == "narration" and b.text
        )
        leaked_quote_id = self._first_verbatim_leak(narration_text, quotes_by_id)
        if leaked_quote_id is not None:
            failures.append(
                ValidationFailure(
                    reason="verbatim_leak",
                    detail=(
                        f"narration block contains an 8-word verbatim slice from"
                        f" quote_id={leaked_quote_id}; cue the quote instead"
                    ),
                )
            )

        narration_words = word_count(narration_text)
        low = int(narration_word_budget * (1 - WORD_BUDGET_TOLERANCE))
        high = int(narration_word_budget * (1 + WORD_BUDGET_TOLERANCE))
        if narration_words < low:
            failures.append(
                ValidationFailure(
                    reason="word_budget_low",
                    detail=(
                        f"narration={narration_words} words; budget"
                        f" {narration_word_budget}±{int(WORD_BUDGET_TOLERANCE * 100)}%"
                        f" → {low}..{high}"
                    ),
                )
            )
        elif narration_words > high:
            failures.append(
                ValidationFailure(
                    reason="word_budget_high",
                    detail=(
                        f"narration={narration_words} words; budget"
                        f" {narration_word_budget}±{int(WORD_BUDGET_TOLERANCE * 100)}%"
                        f" → {low}..{high}"
                    ),
                )
            )

        return tuple(failures)

    @staticmethod
    def _first_verbatim_leak(
        narration: str, quotes_by_id: Mapping[str, QuoteCandidate]
    ) -> Optional[str]:
        if not narration:
            return None
        narration_norm = _normalise_for_match(narration)
        for q in quotes_by_id.values():
            quote_norm = _normalise_for_match(q.text)
            quote_words = quote_norm.split()
            if len(quote_words) < VERBATIM_LEAK_NGRAM:
                continue
            for start in range(0, len(quote_words) - VERBATIM_LEAK_NGRAM + 1):
                slice_text = " ".join(quote_words[start : start + VERBATIM_LEAK_NGRAM])
                if slice_text and slice_text in narration_norm:
                    return q.quote_id
        return None

    @staticmethod
    def _tighten_prompt(
        original_prompt: str,
        failures: Sequence[ValidationFailure],
        narration_word_budget: int,
    ) -> str:
        bullet = "\n".join(f"- {f.reason}: {f.detail}" for f in failures)
        return (
            f"{original_prompt}\n\n"
            "RETRY: your previous output failed validation. Fix every issue below"
            " and emit the corrected JSON only.\n"
            f"{bullet}\n\n"
            "Reminders for the retry:\n"
            f"- Stay within {narration_word_budget} narration words"
            f" (±{int(WORD_BUDGET_TOLERANCE * 100)}%, counted across narration"
            " block text only).\n"
            "- Every kind=quote block's quote_id must come from the supplied"
            " pool — do not invent ids.\n"
            "- Never copy a quote's verbatim text into a narration block; cue"
            " the quote at the right beat instead.\n"
        )


_PUNCTUATION_RE = re.compile(r"[\W_]+", re.UNICODE)


def _normalise_for_match(text: str) -> str:
    """Lowercase + collapse non-word runs to single spaces.

    Used by the verbatim-leak check so that re-cased or re-punctuated
    quote slices still trigger the contract — "It's the best time" and
    "its the best time" are treated as the same string for matching.
    """
    return _PUNCTUATION_RE.sub(" ", text.lower()).strip()
