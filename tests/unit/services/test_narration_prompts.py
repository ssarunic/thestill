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

"""Anchor prompt registry (spec #77 §3, §7)."""

import pytest

from thestill.services.narration_prompts import (
    DEFAULT_ANCHOR_PROMPT_NAME,
    NOISE_PHRASES,
    available_anchor_prompts,
    count_noise_phrase_hits,
    load_anchor_prompt,
    load_default_anchor_prompt,
)


def test_all_voices_ship_and_default_is_conversational_v2() -> None:
    assert {"conversational_anchor", "conversational_v2", "newsroom_anchor"} <= set(available_anchor_prompts())
    assert DEFAULT_ANCHOR_PROMPT_NAME == "conversational_v2"
    assert load_default_anchor_prompt() == load_anchor_prompt("conversational_v2")


def test_conversational_prompt_embeds_the_noise_list_from_code() -> None:
    text = load_anchor_prompt("conversational_anchor")
    assert "{{noise_phrases}}" not in text
    for phrase in NOISE_PHRASES:
        assert f'"{phrase}"' in text
    # The output contract the writer validates against is unchanged.
    assert "`blocks`" in text and '"opener"' in text and "quote_id" in text


def test_newsroom_prompt_keeps_contract_and_budget_ceiling() -> None:
    text = load_anchor_prompt("newsroom_anchor")
    assert "hard ceiling" in text and "`blocks`" in text


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "conversational_anchor.md", "Nope", "no-dash", "missing_voice"])
def test_bad_prompt_names_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        load_anchor_prompt(bad)


def test_noise_phrase_hits_are_case_and_punctuation_insensitive() -> None:
    text = "The Landscape is shifting. It's worth noting, notably, that we must navigate — and unpack."
    # the landscape, it's worth noting, notably, navigate, unpack
    assert count_noise_phrase_hits(text) == 5
    assert count_noise_phrase_hits("Azeem said the models are four months behind.") == 0
    assert count_noise_phrase_hits("") == 0


def test_v2_prompt_carries_the_reaction_contract_and_noise_list() -> None:
    text = load_anchor_prompt("conversational_v2")
    assert "{{noise_phrases}}" not in text
    assert '"reaction"' in text and "exactly one reaction block" in text
    assert "transition:" in text and '"great point"' in text
    assert "`blocks`" in text and "quote_id" in text
