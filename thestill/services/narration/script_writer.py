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
from ...utils.text_sanitizer import sanitize_text
from ..narration_prompts import count_noise_phrase_hits, find_noise_phrases
from .claim_selector import quote_fits_claim, select_claim
from .models import (
    REACTION_MAX_WORDS,
    EpisodeBrief,
    QuoteCandidate,
    ScriptBlock,
    ScriptBlockKind,
    Segment,
    ThemePlan,
    ValidationFailure,
    word_count,
)
from .register import addresses_listener, has_first_person, unquote_scare_quotes

logger = get_logger(__name__)


# Validation tolerances. The bounds are asymmetric: overruns blow the
# spoken-duration budget that downstream TTS plans against, while
# undershoots just produce a shorter briefing — preferable to a
# fallback link-index for the user. LLMs reliably undershoot long
# prompts (Gemini in particular on 1k+ word targets), so the low side
# is generous.
WORD_BUDGET_TOLERANCE_HIGH = 0.15  # +15% cap (strict — overruns hurt TTS)
WORD_BUDGET_TOLERANCE_LOW = 0.50  # -50% floor (lenient — short briefing > fallback)
VERBATIM_LEAK_NGRAM = 8  # an 8-word verbatim slice from a quote in narration → leak
MAX_REGENERATIONS = 1  # one retry, then fall back
# Spec #77 §4 — the writer is told a target below the validation ceiling.
# With the richer material it lands at 105–125 % of the number it is
# given; stating 80 % keeps the result inside the +15 % window without
# loosening what TTS budgets against.
DEFAULT_STATED_TARGET_RATIO = 0.8


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
    noise_phrase_hits: int = 0
    stated_word_target: int = 0
    reaction_count: int = 0
    reactions_missing: int = 0


class ScriptWriter:
    """Generate the anchor-voiced script with built-in validation + retry.

    ``write(...)`` returns the validated script blocks (with
    ``failures=()``) on success, or the latest failures alongside an
    empty block list when both the initial call and the regeneration
    fail. The generator interprets the empty-blocks result as the
    fallback signal.
    """

    def __init__(
        self,
        provider: LLMProvider,
        system_prompt: str,
        wpm: float = 150.0,
        stated_target_ratio: float = DEFAULT_STATED_TARGET_RATIO,
        material_max_words: int = 400,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self.wpm = wpm
        self.stated_target_ratio = stated_target_ratio
        self.material_max_words = material_max_words

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
        # The model is told ``stated``; ``_validate`` keeps the real budget.
        stated = max(1, int(narration_word_budget * self.stated_target_ratio))
        user_prompt = self._build_user_prompt(plan, briefs_by_id, quotes, stated)
        attempts: List[Tuple[Tuple[ScriptBlock, ...], Tuple[ValidationFailure, ...], int]] = []
        # An attempt that failed only soft rules is a usable script. Keep
        # the first one so a retry that trips a hard rule (the old voice
        # overshooting the budget while fixing a soft miss) falls back to
        # it instead of to the link index.
        soft_only: Optional[Tuple[List[ScriptBlock], int, List[ValidationFailure]]] = None
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
            raw_words = sum(word_count(b.text) for b in blocks if b.kind in ("narration", "reaction") and b.text)
            hard = [f for f in failures if not f.soft]
            last_attempt = attempt >= MAX_REGENERATIONS
            # Soft failures (the reaction rule) get the retry but never the
            # fallback: a briefing without a reaction beats no briefing.
            soft = [f for f in failures if f.soft]
            if not failures or (not hard and last_attempt):
                if soft:
                    logger.info(
                        "narration: script accepted with soft failures",
                        attempt=attempt,
                        failures=[f.reason for f in soft],
                    )
                return self._accept(blocks, raw_words, stated, soft)
            if not hard and soft_only is None:
                soft_only = (blocks, raw_words, soft)

            logger.info(
                "narration: script validation failed",
                attempt=attempt,
                failures=[f.reason for f in failures],
            )
            attempts.append((tuple(blocks), tuple(failures), raw_words))
            if attempt < MAX_REGENERATIONS:
                user_prompt = self._tighten_prompt(user_prompt, failures, stated)

        if soft_only is not None:
            blocks, raw_words, soft = soft_only
            logger.info(
                "narration: retry failed a hard rule; keeping the earlier soft-only attempt",
                failures=[f.reason for f in soft],
            )
            return self._accept(blocks, raw_words, stated, soft)
        # Both attempts failed (or LLM error path). Surface the latest
        # failure list to the caller — the generator will trigger the
        # link-index fallback and log a ``narration.fallback`` event.
        last = attempts[-1] if attempts else ((), (), 0)
        return ScriptResult(blocks=(), failures=last[1], raw_word_count=last[2])

    @staticmethod
    def _accept(
        blocks: Sequence[ScriptBlock],
        raw_words: int,
        stated: int,
        soft: Sequence[ValidationFailure],
    ) -> ScriptResult:
        spoken = " ".join(b.text for b in blocks if b.kind in ("narration", "reaction") and b.text)
        return ScriptResult(
            blocks=tuple(blocks),
            failures=(),
            raw_word_count=raw_words,
            noise_phrase_hits=count_noise_phrase_hits(spoken),
            stated_word_target=stated,
            reaction_count=sum(1 for b in blocks if b.kind == "reaction"),
            reactions_missing=sum(1 for f in soft if f.reason == "reaction_missing"),
        )

    def _build_user_prompt(
        self,
        plan: ThemePlan,
        briefs_by_id: Mapping[str, EpisodeBrief],
        quotes: Sequence[QuoteCandidate],
        narration_word_budget: int,
    ) -> str:
        # Spec #77 Phase 2b: one claim per episode, chosen against its
        # segment angle; quotes are marked by whether they illustrate it.
        claims: Dict[str, str] = {}
        for seg in plan.segments:
            for eid in seg.episode_ids:
                brief = briefs_by_id.get(eid)
                chosen = select_claim(brief, seg.angle, max_words=self.material_max_words) if brief else None
                if chosen is not None:
                    claims[eid] = chosen.claim + (" " + chosen.colour if chosen.colour else "")
        parts: List[str] = [
            f"Narration word budget: aim for {narration_word_budget} words"
            f" (hard cap: +{int(WORD_BUDGET_TOLERANCE_HIGH * 100)}% — going over"
            " blows the spoken-duration budget; counts only narration block text,"
            " quote blocks are excluded).",
            "",
            "Quote pool (verbatim — do not retype these in your narration;"
            " cue a quote only when fits_claim=yes, or skip the clip for that show):",
        ]
        if quotes:
            for q in quotes:
                fit = "yes" if quote_fits_claim(q.text, claims.get(q.episode_id)) else "no"
                parts.append(
                    f"- quote_id={q.quote_id} | speaker={q.speaker} ({q.speaker_role})"
                    f" | podcast={q.podcast_title} | episode_id={q.episode_id} | fits_claim={fit}"
                )
                parts.append(f"  text: {q.text}")
        else:
            parts.append("- (none — produce opener, tail, and signoff only)")
        parts.append("")

        if plan.segments:
            parts.append("Segment plan:")
            for seg in plan.segments:
                parts.append(self._format_segment(seg, briefs_by_id, self.material_max_words))
        else:
            parts.append("Segment plan: (empty — emit opener, tail-only narration, signoff)")

        if plan.tail_ids:
            parts.append("")
            parts.append("Tail bucket (rapid-fire mentions):")
            for eid in plan.tail_ids:
                brief = briefs_by_id.get(eid)
                if brief is None:
                    continue
                parts.append(f"- episode_id={eid} | podcast={brief.podcast_title}" f" | title={brief.episode_title}")

        return "\n".join(parts)

    @staticmethod
    def _format_segment(seg: Segment, briefs_by_id: Mapping[str, EpisodeBrief], material_max_words: int = 400) -> str:
        lines = [
            "",
            f"Segment {seg.rank}: {seg.theme}",
            f"  angle: {seg.angle}",
            f"  section: segment-{seg.rank}",
            f"  transition: {_transition_instruction(seg)}",
        ]
        for eid in seg.episode_ids:
            brief = briefs_by_id.get(eid)
            if brief is None:
                continue
            lines.append(f"  - episode_id={eid} | podcast={brief.podcast_title}" f" | title={brief.episode_title}")
            if brief.guests:
                lines.append(f"    guests: {', '.join(brief.guests)}")
            # Spec #77 Phase 2b: one claim per show, chosen against the
            # angle, plus one piece of colour. Legacy summaries fall back
            # to the gist.
            chosen = select_claim(brief, seg.angle, max_words=material_max_words)
            if chosen is not None:
                lines.append(f"    claim: {chosen.claim}")
                if chosen.colour:
                    lines.append(f"    colour: {chosen.colour}")
            elif brief.gist:
                lines.append(f"    gist: {brief.gist}")
        return "\n".join(lines)

    @staticmethod
    def _normalise_blocks(
        out_blocks: Sequence[_ScriptBlockOut],
        quotes_by_id: Mapping[str, QuoteCandidate],
        wpm: float,
    ) -> List[ScriptBlock]:
        blocks: List[ScriptBlock] = []
        removed_total = 0
        for raw in out_blocks:
            if raw.kind in ("narration", "reaction"):
                # Spec #42 guard at the seam where model text becomes a
                # script block (the 2026-07-02 control-byte incident).
                clean, removed = sanitize_text(raw.text or "")
                removed_total += removed
                # Spec #77 Phase 2b req. 5: scare quotes never reach the
                # page; the phrase is owned or the quotes are dropped.
                text = unquote_scare_quotes(clean).strip() or None
                duration = word_count(text) / wpm * 60.0 if text and wpm else 0.0
                blocks.append(
                    ScriptBlock(
                        kind=raw.kind,
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
        if removed_total:
            logger.warning("narration.script_output_sanitized", removed_count=removed_total)
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
                        detail=(f"block {idx} (kind=quote) references unknown quote_id=" f"{b.quote_id!r}"),
                    )
                )

        # An 8-word slice that appears verbatim in any quote is the
        # paraphrase-leak signal — the model copied a quote into the
        # narration instead of cueing it. Spec #33 §"Script Generation".
        narration_text = " ".join(b.text for b in blocks if b.kind in ("narration", "reaction") and b.text)
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
        low = int(narration_word_budget * (1 - WORD_BUDGET_TOLERANCE_LOW))
        high = int(narration_word_budget * (1 + WORD_BUDGET_TOLERANCE_HIGH))
        if narration_words < low:
            failures.append(
                ValidationFailure(
                    reason="word_budget_low",
                    detail=(
                        f"narration={narration_words} words; budget"
                        f" {narration_word_budget} (-{int(WORD_BUDGET_TOLERANCE_LOW * 100)}%"
                        f"/+{int(WORD_BUDGET_TOLERANCE_HIGH * 100)}%) → {low}..{high}"
                    ),
                )
            )
        elif narration_words > high:
            failures.append(
                ValidationFailure(
                    reason="word_budget_high",
                    detail=(
                        f"narration={narration_words} words; budget"
                        f" {narration_word_budget} (-{int(WORD_BUDGET_TOLERANCE_LOW * 100)}%"
                        f"/+{int(WORD_BUDGET_TOLERANCE_HIGH * 100)}%) → {low}..{high}"
                    ),
                )
            )

        failures.extend(self._validate_reactions(blocks))
        failures.extend(self._validate_register(blocks))
        return tuple(failures)

    @staticmethod
    def _validate_register(blocks: Sequence[ScriptBlock]) -> List[ValidationFailure]:
        """Spec #77 Phase 2b register rules the code can check. All soft."""
        out: List[ValidationFailure] = []
        sections: Dict[str, List[str]] = {}
        for b in blocks:
            if b.kind in ("narration", "reaction") and b.text and b.section.startswith("segment-"):
                sections.setdefault(b.section, []).append(b.text)
        for section, texts in sections.items():
            joined = " ".join(texts)
            if not has_first_person(joined):
                out.append(
                    ValidationFailure(
                        reason="segment_no_first_person",
                        detail=(
                            f"{section} has no first-person sentence; add one specific reaction"
                            ' of your own ("this one surprised me", "not sure I buy this")'
                        ),
                        soft=True,
                    )
                )
            if not addresses_listener(joined):
                out.append(
                    ValidationFailure(
                        reason="segment_no_stakes",
                        detail=f"{section} never addresses the listener; end it with one why-you'd-care line said to 'you'",
                        soft=True,
                    )
                )
        spoken = " ".join(b.text for b in blocks if b.kind in ("narration", "reaction") and b.text)
        hits = find_noise_phrases(spoken)
        if hits:
            out.append(
                ValidationFailure(
                    reason="noise_phrases",
                    detail="remove these phrases: " + ", ".join(f"'{h}'" for h in sorted(set(hits))),
                    soft=True,
                )
            )
        return out

    @staticmethod
    def _validate_reactions(blocks: Sequence[ScriptBlock]) -> List[ValidationFailure]:
        """Spec #77 Phase 2b: every quote cue is followed by one short reaction.

        Soft failures: they earn the retry, never the fallback.
        """
        out: List[ValidationFailure] = []
        for idx, b in enumerate(blocks):
            if b.kind != "quote":
                continue
            nxt = blocks[idx + 1] if idx + 1 < len(blocks) else None
            if nxt is None or nxt.kind != "reaction" or not nxt.text:
                out.append(
                    ValidationFailure(
                        reason="reaction_missing",
                        detail=(
                            f"quote {b.quote_id} (block {idx}) must be followed by one"
                            " kind=reaction block: a single spoken sentence reacting to the clip"
                        ),
                        soft=True,
                    )
                )
                continue
            if nxt.section != b.section:
                out.append(
                    ValidationFailure(
                        reason="reaction_section_mismatch",
                        detail=f"reaction after quote {b.quote_id} must share section {b.section!r}",
                        soft=True,
                    )
                )
            if word_count(nxt.text) > REACTION_MAX_WORDS:
                out.append(
                    ValidationFailure(
                        reason="reaction_too_long",
                        detail=(
                            f"reaction after quote {b.quote_id} is {word_count(nxt.text)} words;"
                            f" keep it to one sentence under {REACTION_MAX_WORDS}"
                        ),
                        soft=True,
                    )
                )
        return out

    @staticmethod
    def _first_verbatim_leak(narration: str, quotes_by_id: Mapping[str, QuoteCandidate]) -> Optional[str]:
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
            f"- Aim for {narration_word_budget} narration words; do not exceed"
            f" +{int(WORD_BUDGET_TOLERANCE_HIGH * 100)}% (counted across narration"
            " block text only).\n"
            "- Every kind=quote block's quote_id must come from the supplied"
            " pool — do not invent ids.\n"
            "- Never copy a quote's verbatim text into a narration block; cue"
            " the quote at the right beat instead.\n"
        )


_PUNCTUATION_RE = re.compile(r"[\W_]+", re.UNICODE)


def _transition_instruction(seg: Segment) -> str:
    """How the writer may move between the shows in ``seg`` (spec #77 Phase 2b)."""
    if len(seg.episode_ids) < 2 or seg.relationship == "none":
        return (
            "these shows are not related; do not bridge them. Use a hard cut or"
            " one of: 'completely different thing', 'okay, <topic>', 'meanwhile'"
        )
    return (
        f"relationship={seg.relationship}: name it plainly in one sentence"
        " (e.g. 'X and Y basically disagree on this'), then continue"
    )


def _normalise_for_match(text: str) -> str:
    """Lowercase + collapse non-word runs to single spaces.

    Used by the verbatim-leak check so that re-cased or re-punctuated
    quote slices still trigger the contract — "It's the best time" and
    "its the best time" are treated as the same string for matching.
    """
    return _PUNCTUATION_RE.sub(" ", text.lower()).strip()
