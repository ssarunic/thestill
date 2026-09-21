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

"""Stage 2 - the LLM picks among the offered candidates (spec #81).

The model never supplies an identifier of its own: it answers with the
QID of a listed candidate or with none, and the validator discards
anything else. Names are addressed by a short id (``n1``) so the answer
does not depend on the model echoing a spoken name exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set

from pydantic import BaseModel, Field, field_validator
from structlog import get_logger

from ...utils.prompt_safety import UNTRUSTED_CONTENT_PREAMBLE, wrap_untrusted
from ...utils.text_sanitizer import sanitize_text
from ..llm_provider import LLMProvider
from .types import Candidate, ChoiceDecision, LinkContext, NameGroup

logger = get_logger(__name__)

# Bump when the prompt changes meaning: cached decisions made under another
# version are re-decided as their names come up.
PROMPT_VERSION = "p1"

BATCH_SIZE = 40
REASK_BATCH_SIZE = 10
MAX_EXCERPTS = 3
EXCERPT_MAX_CHARS = 400
DESCRIPTION_MAX_CHARS = 160
MAX_ANCHORS = 12
MAX_OUTPUT_TOKENS = 8192

SYSTEM_PROMPT = """You link names spoken in a podcast episode to Wikidata entities.

For each numbered name you get: the name as transcribed, the kind of thing \
the transcript tagger thought it was, up to three excerpts where it was \
spoken, and a list of candidate Wikidata entities (QID, label, description).

For each name, decide which candidate the speakers mean.

Rules:
- Answer with the QID of one listed candidate for that name, or null. Never \
answer with a QID that is not in that name's candidate list.
- Answer null when no candidate fits, when the name is a generic word and not \
a specific entity, or when the excerpts do not settle which candidate is meant.
- Use the podcast, the episode title and the known participants as context. A \
film discussed by name is the film, not the person it is named after.
- A label that matches exactly is not enough: the description must fit how \
the name is used in the excerpts.
- confidence is "high" when the excerpts make the choice clear, "medium" when \
it is the most plausible reading, "low" when it is a guess.
- reason is one short sentence.

Return one entry per name id. Do not add, drop or rename ids."""


class Choice(BaseModel):
    id: str = Field(description="The name id from the request, e.g. 'n3'.")
    qid: Optional[str] = Field(default=None, description="QID of one listed candidate, or null.")
    confidence: Literal["high", "medium", "low"] = "low"
    reason: str = Field(default="", description="One short sentence.")

    @field_validator("qid", "reason", mode="after")
    @classmethod
    def _strip_control_chars(cls, value: Optional[str]) -> Optional[str]:
        """Scrub control characters at the schema boundary, and say so: silent
        stripping would hide a provider regression (spec #42 FM-4)."""
        if value is None:
            return None
        clean, removed = sanitize_text(value)
        if removed:
            logger.warning("llm_control_chars_stripped", removed=removed, stage="entity_linking")
        return clean.strip()


class ChooserResponse(BaseModel):
    choices: List[Choice] = Field(default_factory=list, description="One entry per name id.")


@dataclass
class ChoiceOutcome:
    """``unanswered`` names have no decision and must stay pending.
    ``call_errors`` separates "the provider could not be reached" from "the
    provider answered and left names out"."""

    decisions: Dict[str, ChoiceDecision] = field(default_factory=dict)
    unanswered: Set[str] = field(default_factory=set)
    llm_calls: int = 0
    call_errors: int = 0


class LLMCandidateChooser:
    def __init__(self, provider: LLMProvider):
        self._provider = provider

    @property
    def version(self) -> str:
        return f"{PROMPT_VERSION}:{self._provider.get_model_name()}"

    def choose(
        self,
        groups: List[NameGroup],
        candidates: Dict[str, List[Candidate]],
        context: LinkContext,
    ) -> ChoiceOutcome:
        """Ask about every name, then once more, in smaller batches, about
        any the model left out."""
        outcome = ChoiceOutcome()
        remaining = self._ask(groups, candidates, context, BATCH_SIZE, outcome)
        if remaining:
            remaining = self._ask(remaining, candidates, context, REASK_BATCH_SIZE, outcome)
        outcome.unanswered = {g.surface_key for g in remaining}
        return outcome

    def _ask(
        self,
        groups: List[NameGroup],
        candidates: Dict[str, List[Candidate]],
        context: LinkContext,
        batch_size: int,
        outcome: ChoiceOutcome,
    ) -> List[NameGroup]:
        """Returns the groups still without an answer."""
        missing: List[NameGroup] = []
        for start in range(0, len(groups), batch_size):
            batch = groups[start : start + batch_size]
            by_id = {f"n{i + 1}": group for i, group in enumerate(batch)}
            outcome.llm_calls += 1
            try:
                response = self._provider.generate_structured(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT + UNTRUSTED_CONTENT_PREAMBLE},
                        {"role": "user", "content": build_user_message(by_id, candidates, context)},
                    ],
                    response_model=ChooserResponse,
                    temperature=0.0 if self._provider.supports_temperature() else None,
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
            except Exception as exc:  # pylint: disable=broad-except
                # Provider SDKs raise their own types, and a refusal or a
                # truncated answer surfaces here too. Whatever it was, these
                # names have no answer; the linker decides what that means.
                outcome.call_errors += 1
                logger.warning(
                    "entity_linking_chooser_call_failed",
                    episode_id=context.episode_id,
                    error_type=type(exc).__name__,
                    names=len(batch),
                )
                missing.extend(batch)
                continue
            answered: Set[str] = set()
            for choice in response.choices:
                group = by_id.get(choice.id)
                if group is None or choice.id in answered:
                    continue  # an id we never sent, or a duplicate: ignore
                answered.add(choice.id)
                outcome.decisions[group.surface_key] = ChoiceDecision(
                    surface_key=group.surface_key,
                    qid=choice.qid or None,
                    confidence=choice.confidence,
                    reason=choice.reason,
                )
            missing.extend(group for name_id, group in by_id.items() if name_id not in answered)
        return missing


def build_user_message(
    by_id: Dict[str, NameGroup], candidates: Dict[str, List[Candidate]], context: LinkContext
) -> str:
    """Everything here was spoken, scraped or fetched, so all of it is fenced
    as untrusted; only the ids and the layout are ours."""
    header = [
        f"Podcast: {_one_line(context.podcast_title)}",
        f"Episode: {_one_line(context.episode_title)}",
    ]
    if context.anchor_names:
        header.append("Known participants: " + "; ".join(_one_line(n) for n in context.anchor_names[:MAX_ANCHORS]))
    blocks = [wrap_untrusted("\n".join(header), label="EPISODE")]
    for name_id, group in by_id.items():
        lines = [f"name: {_one_line(group.surface_form)}", f"tagged as: {group.surface_label or 'unknown'}"]
        for excerpt in group.excerpts[:MAX_EXCERPTS]:
            lines.append(f"excerpt: {_one_line(excerpt)[:EXCERPT_MAX_CHARS]}")
        lines.append("candidates:")
        for candidate in candidates.get(group.surface_key, []):
            description = _one_line(candidate.description)[:DESCRIPTION_MAX_CHARS] or "(no description)"
            lines.append(f"- {candidate.qid} | {_one_line(candidate.label)} | {description}")
        blocks.append(f"[{name_id}]\n" + wrap_untrusted("\n".join(lines), label="NAME"))
    return "\n\n".join(blocks)


def _one_line(text: str) -> str:
    return " ".join((text or "").split())
