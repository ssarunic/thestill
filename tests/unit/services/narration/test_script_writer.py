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

from thestill.services.narration.models import (
    EpisodeBrief,
    QuoteCandidate,
    Segment,
    ThemePlan,
)
from thestill.services.narration.script_writer import ScriptResult, ScriptWriter

from tests.conftest import MockLLMProvider

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
) -> dict:
    body = (" ".join(["word"] * narration_words)).strip()
    blocks: List[Dict[str, Any]] = [
        {"kind": "narration", "section": "opener", "text": body},
    ]
    if cue_quote:
        blocks.append({"kind": "quote", "section": "segment-1", "quote_id": quote_id})
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
