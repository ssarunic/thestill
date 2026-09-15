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

"""Deterministic register metrics (spec #77 Phase 2b)."""

from thestill.services.narration.models import ScriptBlock, Segment, ThemePlan
from thestill.services.narration.register import classify_transition, measure_register


def _n(section: str, text: str) -> ScriptBlock:
    return ScriptBlock(kind="narration", section=section, text=text)


def test_measures_first_person_reportage_scare_quotes_and_reactions() -> None:
    blocks = [
        _n("opener", "So, imagine being thirteen and hearing that."),
        _n("segment-1", "Tom says the fear never goes away. He told Jamie that you use it. I think that's right."),
        ScriptBlock(kind="quote", section="segment-1", quote_id="q1"),
        ScriptBlock(kind="reaction", section="segment-1", text="Which, honestly, is the bit worth stealing."),
        _n(
            "segment-2",
            "Completely different thing. Mo calls it a 'one in, one out' policy. According to him it lasted years.",
        ),
        ScriptBlock(kind="quote", section="segment-2", quote_id="q2"),
        _n("segment-2", "And then the next fact with no reaction."),
        _n("signoff", "Catch you tomorrow."),
    ]
    plan = ThemePlan(
        segments=(
            Segment(theme="A", angle="a", episode_ids=("e1",), rank=1),
            Segment(theme="B", angle="b", episode_ids=("e2",), rank=2),
        ),
        tail_ids=(),
    )
    m = measure_register(blocks, plan)
    assert m.sentences == 10
    assert m.first_person_sentences == 1 and 0 < m.first_person_ratio < 0.2
    assert m.reportage_sentences == 2  # "He told Jamie", "According to him"
    assert m.scare_quotes == 1
    assert m.clips == 2 and m.clips_with_reaction == 1
    assert m.segments == 2 and m.segments_with_first_person == 1
    assert m.transitions == {"permitted": 1} and m.bridges_unearned == 0
    assert m.sentence_len_p50 >= 4 and m.sentence_len_p90 >= m.sentence_len_p50


def test_bridge_into_an_untyped_segment_is_unearned_but_a_named_relationship_is_not() -> None:
    blocks = [
        _n("segment-1", "First show."),
        _n("segment-2", "That shift toward specialised engineering is exactly what the next guest saw."),
        _n("segment-3", "Baseten and Chai basically disagree on this."),
    ]
    plan = ThemePlan(
        segments=(
            Segment(theme="A", angle="a", episode_ids=("e1",), rank=1),
            Segment(theme="B", angle="b", episode_ids=("e2",), rank=2),
            Segment(theme="C", angle="c", episode_ids=("e3", "e4"), rank=3, relationship="debate"),
        ),
        tail_ids=(),
    )
    m = measure_register(blocks, plan)
    assert m.transitions == {"bridge": 1, "named_relationship": 1}
    assert m.bridges_unearned == 1


def test_classify_transition_examples() -> None:
    assert classify_transition("Okay, physical AI.") == "permitted"
    assert classify_transition("Meanwhile, Neal Arthur says brands can't outsource soul.") == "permitted"
    assert classify_transition("Speaking of money, Scott had thoughts.") == "bridge"
    assert classify_transition("Sanjit Biswas thinks we're looking at the wrong things.") == "hard_cut"


def test_empty_script_measures_zero() -> None:
    m = measure_register([])
    assert m.sentences == 0 and m.first_person_ratio == 0.0 and m.transitions == {}


def test_helpers_first_person_listener_and_unquote() -> None:
    from thestill.services.narration.register import addresses_listener, has_first_person, unquote_scare_quotes

    assert has_first_person("Not sure I buy this.") and not has_first_person("He is sure.")
    assert addresses_listener("You'd care because it's your money.") and not addresses_listener("They care.")
    assert (
        unquote_scare_quotes("the 'open everything' crowd don't 'get' it") == "the open everything crowd don't get it"
    )
    assert (
        unquote_scare_quotes("a 'very long quoted span of many words here' stays")
        == "a 'very long quoted span of many words here' stays"
    )
