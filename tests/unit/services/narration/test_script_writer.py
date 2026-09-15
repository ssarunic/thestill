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

"""Tests for the anchor-prose script writer (spec #33 Phase 2 stage 4)."""

import json
from typing import Any, Dict, List, Optional, Type

import pytest
from pydantic import BaseModel

from tests.conftest import MockLLMProvider
from thestill.services.narration.models import EpisodeBrief, QuoteCandidate, Segment, ThemePlan
from thestill.services.narration.script_writer import ScriptResult, ScriptWriter

_SYSTEM_PROMPT = "TEST ANCHOR PROMPT"


def _quote(qid: str = "q1", episode_id: str = "ep-1", text: Optional[str] = None) -> QuoteCandidate:
    return QuoteCandidate(
        quote_id=qid,
        episode_id=episode_id,
        podcast_title="Pod",
        speaker="Alex Anchor",
        speaker_role="host",
        text=text or "It's the best time to be a junior engineer in this industry.",
        start_seconds=60.0,
        duration_seconds=12.0,
    )


def _plan() -> ThemePlan:
    return ThemePlan(
        segments=(
            Segment(
                theme="AI coding agents",
                angle="Two PMs disagree about shipping by non-engineers",
                episode_ids=("ep-1",),
                rank=1,
            ),
        ),
        tail_ids=(),
    )


def _briefs() -> Dict[str, EpisodeBrief]:
    return {
        "ep-1": EpisodeBrief(
            episode_id="ep-1",
            podcast_title="Pod",
            episode_title="Lead Episode",
            gist="Compact gist.",
        )
    }


class _ScriptedProvider(MockLLMProvider):
    """Provider that yields a pre-canned sequence of structured responses."""

    def __init__(self, responses: List[Any], model_name: str = "mock-model") -> None:
        super().__init__(model_name=model_name)
        self._queue = list(responses)
        self.call_count = 0
        self.last_messages = None

    def generate_structured(  # type: ignore[override]
        self,
        messages,
        response_model: Type[BaseModel],
        temperature=None,
        max_tokens=None,
    ):
        self.call_count += 1
        self.last_messages = messages
        if not self._queue:
            raise AssertionError("ScriptedProvider exhausted")
        payload = self._queue.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, str):
            payload = json.loads(payload)
        return response_model(**payload)


def _good_response(
    *,
    narration_words: int = 100,
    cue_quote: bool = True,
    quote_id: str = "q1",
    reaction: str | None = "Which, honestly, is the bit worth stealing.",
) -> dict:
    body = (" ".join(["word"] * narration_words)).strip()
    blocks: List[Dict[str, Any]] = [
        {"kind": "narration", "section": "opener", "text": body},
    ]
    if cue_quote:
        blocks.append({"kind": "quote", "section": "segment-1", "quote_id": quote_id})
        if reaction:
            blocks.append({"kind": "reaction", "section": "segment-1", "text": reaction})
    return {"blocks": blocks}


def test_validates_and_returns_blocks_on_success() -> None:
    provider = _ScriptedProvider([_good_response(narration_words=100)])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[_quote()],
        narration_word_budget=100,
    )
    assert isinstance(result, ScriptResult)
    assert result.failures == ()
    assert any(b.kind == "narration" for b in result.blocks)
    assert any(b.kind == "quote" for b in result.blocks)
    assert provider.call_count == 1


def test_unknown_quote_id_triggers_regeneration_then_recovers() -> None:
    bad = {
        "blocks": [
            {"kind": "narration", "section": "opener", "text": " ".join(["word"] * 100)},
            {"kind": "quote", "section": "segment-1", "quote_id": "q-hallucinated"},
        ]
    }
    good = _good_response(narration_words=100, quote_id="q1")
    provider = _ScriptedProvider([bad, good])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[_quote()],
        narration_word_budget=100,
    )
    assert provider.call_count == 2
    assert result.failures == ()
    quote_block = next(b for b in result.blocks if b.kind == "quote")
    assert quote_block.quote_id == "q1"


def test_word_budget_violation_falls_back_after_two_failed_attempts() -> None:
    too_long = _good_response(narration_words=400)
    too_long_again = _good_response(narration_words=400)
    provider = _ScriptedProvider([too_long, too_long_again])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[_quote()],
        narration_word_budget=100,
    )
    assert provider.call_count == 2
    assert result.blocks == ()
    reasons = {f.reason for f in result.failures}
    assert "word_budget_high" in reasons


def test_verbatim_leak_fails_validation() -> None:
    quote = _quote(text="It's the best time to be a junior in tech today and tomorrow.")
    leak = {
        "blocks": [
            {
                "kind": "narration",
                "section": "opener",
                "text": (
                    "Today's lead story echoes a familiar refrain: "
                    "it's the best time to be a junior in tech today "
                    "and tomorrow says one of our guests."
                ),
            },
            {"kind": "quote", "section": "segment-1", "quote_id": "q1"},
        ]
    }
    provider = _ScriptedProvider([leak, leak])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[quote],
        narration_word_budget=40,
    )
    assert result.blocks == ()
    assert "verbatim_leak" in {f.reason for f in result.failures}


def test_llm_error_returns_fallback_signal_immediately() -> None:
    provider = _ScriptedProvider([RuntimeError("api down")])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[_quote()],
        narration_word_budget=100,
    )
    assert result.blocks == ()
    assert any(f.reason == "llm_error" for f in result.failures)


def test_zero_budget_short_circuits_without_calling_llm() -> None:
    provider = _ScriptedProvider([])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(),
        briefs_by_id=_briefs(),
        quotes=[_quote()],
        narration_word_budget=0,
    )
    assert provider.call_count == 0
    assert result.blocks == ()
    assert any(f.reason == "empty_blocks" for f in result.failures)


def test_segment_prompt_carries_one_claim_and_colour_and_falls_back_to_gist() -> None:
    provider = _ScriptedProvider([_good_response(narration_words=100)])
    briefs = {
        "ep-1": EpisodeBrief(
            episode_id="ep-1",
            podcast_title="Pod",
            episode_title="Lead Episode",
            gist="Compact gist.",
            takeaways=("AI revenue is real.", "The angle point about shipping."),
            drama=("Round 1: The row over shipping. Tense.",),
        )
    }
    ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=briefs, quotes=[_quote()], narration_word_budget=100
    )
    user_prompt = provider.last_messages[1]["content"]
    assert "claim: The angle point about shipping." in user_prompt  # picked against the angle
    assert "colour: Round 1: The row over shipping." in user_prompt
    assert "AI revenue is real." not in user_prompt  # the other takeaway is dropped, not listed
    assert "gist: Compact gist." not in user_prompt
    assert "transition: these shows are not related; do not bridge" in user_prompt

    provider = _ScriptedProvider([_good_response(narration_words=100)])
    ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert "claim: Compact gist." in provider.last_messages[1]["content"]  # gist fallback becomes the claim


def test_segment_prompt_names_a_typed_relationship() -> None:
    from thestill.services.narration.models import Segment, ThemePlan

    plan = ThemePlan(
        segments=(
            Segment(theme="T", angle="two shows disagree", episode_ids=("ep-1", "ep-2"), rank=1, relationship="debate"),
        ),
        tail_ids=(),
    )
    briefs = {
        "ep-1": EpisodeBrief(episode_id="ep-1", podcast_title="A", episode_title="One", gist="One gist."),
        "ep-2": EpisodeBrief(episode_id="ep-2", podcast_title="B", episode_title="Two", gist="Two gist."),
    }
    provider = _ScriptedProvider([_good_response(narration_words=100)])
    ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=plan, briefs_by_id=briefs, quotes=[_quote()], narration_word_budget=100
    )
    assert "transition: relationship=debate: name it plainly" in provider.last_messages[1]["content"]


def test_stated_target_is_below_the_validated_budget() -> None:
    """Spec #77 §4: the model hears 80 % of the budget; validation keeps the ceiling."""
    provider = _ScriptedProvider([_good_response(narration_words=105)])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert "aim for 80 words" in provider.last_messages[1]["content"]
    # 105 narration + 7 reaction words = 112 ≤ 115: over the stated target, under the ceiling.
    assert result.failures == () and result.stated_word_target == 80
    provider = _ScriptedProvider([_good_response(narration_words=100)])
    ScriptWriter(provider, _SYSTEM_PROMPT, stated_target_ratio=1.0).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert "aim for 100 words" in provider.last_messages[1]["content"]


def test_retry_restates_the_stated_target() -> None:
    provider = _ScriptedProvider(
        [_good_response(narration_words=100, quote_id="q-nope"), _good_response(narration_words=100)]
    )
    ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    retry_prompt = provider.last_messages[1]["content"]
    assert "RETRY" in retry_prompt and "Aim for 80 narration words" in retry_prompt


def test_noise_phrase_hits_are_counted_not_failed() -> None:
    body = "The landscape is shifting, and it's worth noting that, notably, nobody can navigate it. " * 3
    response = {"blocks": [{"kind": "narration", "section": "opener", "text": body}]}
    provider = _ScriptedProvider([response])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[], narration_word_budget=50
    )
    assert result.failures == ()
    assert result.noise_phrase_hits == 12  # four phrases × three repeats


def test_control_bytes_in_model_output_are_stripped() -> None:
    body = "So here is\x00 the thing. " + " ".join(["word"] * 60)
    response = {"blocks": [{"kind": "narration", "section": "opener", "text": body}]}
    provider = _ScriptedProvider([response])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[], narration_word_budget=60
    )
    assert result.failures == ()
    assert "\x00" not in result.blocks[0].text and result.blocks[0].text.startswith("So here is the thing.")


def test_quote_without_reaction_retries_then_is_accepted_with_a_miss_recorded() -> None:
    """Spec #77 Phase 2b: the reaction rule earns the retry, never the fallback."""
    provider = _ScriptedProvider(
        [_good_response(narration_words=100, reaction=None), _good_response(narration_words=100, reaction=None)]
    )
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert provider.call_count == 2
    assert "reaction_missing" in provider.last_messages[1]["content"]
    assert result.failures == () and result.blocks  # accepted, not fallen back
    assert result.reactions_missing == 1 and result.reaction_count == 0


def test_reaction_on_retry_clears_the_miss() -> None:
    provider = _ScriptedProvider(
        [_good_response(narration_words=100, reaction=None), _good_response(narration_words=100)]
    )
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert provider.call_count == 2
    assert result.reactions_missing == 0 and result.reaction_count == 1
    assert [b.kind for b in result.blocks] == ["narration", "quote", "reaction"]


def test_overlong_or_misplaced_reaction_is_soft() -> None:
    long_reaction = " ".join(["word"] * 40)
    response = _good_response(narration_words=60, reaction=long_reaction)  # 100 spoken words total
    response["blocks"][-1]["section"] = "segment-2"
    provider = _ScriptedProvider([response, response])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert provider.call_count == 2 and result.blocks
    retry = provider.last_messages[1]["content"]
    assert "reaction_too_long" in retry and "reaction_section_mismatch" in retry


def test_hard_failure_still_falls_back_even_when_reaction_is_fine() -> None:
    provider = _ScriptedProvider([_good_response(narration_words=200), _good_response(narration_words=200)])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert result.blocks == () and [f.reason for f in result.failures] == ["word_budget_high"]


def test_reaction_words_count_toward_budget_and_leak_check() -> None:
    leak = _quote().text
    response = _good_response(narration_words=60, reaction=leak)
    provider = _ScriptedProvider([response, response])
    result = ScriptWriter(provider, _SYSTEM_PROMPT).write(
        plan=_plan(), briefs_by_id=_briefs(), quotes=[_quote()], narration_word_budget=100
    )
    assert result.blocks == () and "verbatim_leak" in [f.reason for f in result.failures]
