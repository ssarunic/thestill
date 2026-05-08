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

"""Tests for the deterministic quote scorer + selector (spec #33 Phase 1)."""

import pytest

from thestill.services.narration.quote_selector import (
    QuoteSelector,
    QuoteSelectorConfig,
    truncate_to_sentence_prefix,
)
from thestill.services.narration.transcript_loader import ResolvedTurn

from .conftest import make_turn, well_formed_quote_text


def _good_turn(
    start: float,
    *,
    text: str | None = None,
    speaker: str | None = "Jane Doe",
    role: str = "host",
    duration: float = 20.0,
    segment_id: int = 0,
    episode_id: str = "ep-001",
    is_ad_adjacent: bool = False,
) -> ResolvedTurn:
    return make_turn(
        episode_id=episode_id,
        segment_id=segment_id,
        speaker_label="SPEAKER_00" if speaker else None,
        speaker_name=speaker,
        speaker_role=role,
        text=text or well_formed_quote_text(60),
        start_seconds=start,
        end_seconds=start + duration,
        is_ad_adjacent=is_ad_adjacent,
    )


class TestSelectorAttributionGate:
    def test_unresolved_speaker_is_filtered_out(self) -> None:
        turns = [
            _good_turn(60.0, speaker=None, segment_id=1),
            _good_turn(120.0, speaker="Jane Doe", segment_id=2),
        ]
        result = QuoteSelector().select(turns, QuoteSelectorConfig())
        assert len(result) == 1
        assert result[0].speaker == "Jane Doe"

    def test_role_unknown_still_picked_when_name_is_resolved(self) -> None:
        turn = _good_turn(60.0, speaker="Mystery Caller", role="unknown")
        result = QuoteSelector().select([turn], QuoteSelectorConfig())
        assert len(result) == 1
        assert result[0].speaker_role == "unknown"


class TestSelectorLengthFilter:
    def test_sub_minimum_word_count_is_filtered(self) -> None:
        turn = _good_turn(60.0, text="Yes, exactly that.", duration=4.0)
        assert QuoteSelector().select([turn], QuoteSelectorConfig()) == []

    def test_long_turn_is_truncated_to_sentence_prefix(self) -> None:
        long_text = (well_formed_quote_text(40) + " ") * 5
        turn = _good_turn(60.0, text=long_text, duration=120.0)
        result = QuoteSelector().select([turn], QuoteSelectorConfig())
        assert len(result) == 1
        # Truncation must keep us at or below the 90-word cap.
        assert len(result[0].text.split()) <= 95
        # WPM-derived duration is bounded by the 35-second target band.
        assert result[0].duration_seconds <= 36.5


class TestSelectorAdAndSponsorFilters:
    def test_ad_adjacent_turn_is_filtered(self) -> None:
        turn = _good_turn(60.0, is_ad_adjacent=True)
        assert QuoteSelector().select([turn], QuoteSelectorConfig()) == []

    def test_sponsor_mention_inside_boundary_trim_is_dropped(self) -> None:
        # First 5% of a 1000-second episode = first 50s.
        turn = _good_turn(
            10.0,
            text=(
                "This briefing is brought to you by AcmeCorp, the leading "
                "supplier of widgets and gadgets to enterprise teams across "
                "the world. AcmeCorp helps you ship faster and safer."
            ),
            duration=22.0,
        )
        cfg = QuoteSelectorConfig(
            sponsors=("AcmeCorp",),
            episode_duration_seconds=1000.0,
            boundary_trim_fraction=0.05,
        )
        assert QuoteSelector().select([turn], cfg) == []

    def test_sponsor_mention_outside_boundary_is_kept(self) -> None:
        turn = _good_turn(
            500.0,  # mid-episode
            text=(
                "We talked extensively about how AcmeCorp's deployment "
                "tooling has changed the way our team thinks about "
                "production rollouts and risk."
            ),
            duration=22.0,
        )
        cfg = QuoteSelectorConfig(
            sponsors=("AcmeCorp",),
            episode_duration_seconds=1000.0,
            boundary_trim_fraction=0.05,
        )
        result = QuoteSelector().select([turn], cfg)
        assert len(result) == 1


class TestSelectorDiversityCaps:
    def test_per_episode_cap_default_is_two(self) -> None:
        turns = [
            _good_turn(60.0, segment_id=1, speaker="Speaker A"),
            _good_turn(180.0, segment_id=2, speaker="Speaker B"),
            _good_turn(300.0, segment_id=3, speaker="Speaker C"),
        ]
        result = QuoteSelector().select(turns, QuoteSelectorConfig())
        assert len(result) == 2

    def test_per_speaker_cap_is_one_per_episode(self) -> None:
        turns = [
            _good_turn(60.0, segment_id=1, speaker="Solo Speaker"),
            _good_turn(240.0, segment_id=2, speaker="Solo Speaker"),
        ]
        result = QuoteSelector().select(turns, QuoteSelectorConfig())
        assert len(result) == 1

    def test_neighbour_suppression_within_60_seconds(self) -> None:
        turns = [
            _good_turn(60.0, segment_id=1, speaker="Speaker A"),
            _good_turn(80.0, segment_id=2, speaker="Speaker B"),  # 20s later
            _good_turn(200.0, segment_id=3, speaker="Speaker C"),  # 140s later
        ]
        result = QuoteSelector().select(turns, QuoteSelectorConfig())
        # Speaker B sits inside the 60s suppression window of Speaker A;
        # Speaker C is far enough away to land alongside A.
        speakers = [q.speaker for q in result]
        assert "Speaker A" in speakers
        assert "Speaker B" not in speakers


class TestSelectorDeterminism:
    def test_repeat_invocation_is_byte_identical(self) -> None:
        turns = [
            _good_turn(60.0, segment_id=1, speaker="Speaker A"),
            _good_turn(180.0, segment_id=2, speaker="Speaker B"),
            _good_turn(300.0, segment_id=3, speaker="Speaker C"),
        ]
        cfg = QuoteSelectorConfig(keywords=("shipping", "rollout"))
        a = QuoteSelector().select(turns, cfg)
        b = QuoteSelector().select(turns, cfg)
        assert a == b

    def test_input_order_does_not_change_output(self) -> None:
        turns_a = [
            _good_turn(60.0, segment_id=1, speaker="A"),
            _good_turn(180.0, segment_id=2, speaker="B"),
        ]
        turns_b = list(reversed(turns_a))
        cfg = QuoteSelectorConfig()
        out_a = QuoteSelector().select(turns_a, cfg)
        out_b = QuoteSelector().select(turns_b, cfg)
        assert {(q.episode_id, q.start_seconds) for q in out_a} == {
            (q.episode_id, q.start_seconds) for q in out_b
        }


class TestSelfContainmentPenalties:
    def test_pronoun_lead_in_outscored_by_clean_lead_in(self) -> None:
        # Same speaker, same episode, same duration — the only difference
        # is the leading word. Using two separate episodes lets us avoid
        # the per-speaker cap and inspect the score field directly.
        clean = _good_turn(
            60.0,
            text=well_formed_quote_text(40),
            duration=18.0,
            segment_id=1,
            speaker="Alex",
            episode_id="ep-clean",
        )
        pronoun = _good_turn(
            60.0,
            text=(
                "They had no choice but to let the contract expire and "
                "move on to a new vendor by the end of the quarter, all "
                "things considered, despite the obvious risks involved in "
                "switching to a different supplier."
            ),
            duration=18.0,
            segment_id=2,
            speaker="Alex",
            episode_id="ep-pronoun",
        )
        cfg = QuoteSelectorConfig()
        results = QuoteSelector().select([clean, pronoun], cfg)
        scores = {q.episode_id: q.score for q in results}
        assert scores["ep-clean"] > scores["ep-pronoun"]


class TestTruncateHelper:
    def test_returns_full_text_when_under_word_cap(self) -> None:
        text = "Short and well-formed sentence."
        prefix, words = truncate_to_sentence_prefix(text, max_words=10)
        assert prefix == text
        assert words == 4

    def test_clips_to_last_sentence_within_budget(self) -> None:
        text = "First sentence. Second sentence. Third sentence runs longer than the budget allows."
        prefix, _ = truncate_to_sentence_prefix(text, max_words=5)
        # Two complete sentences fit in the 5-word budget; the third
        # spills over so we stop at the second's sentence boundary.
        assert prefix == "First sentence. Second sentence."

    def test_appends_ellipsis_when_no_sentence_in_budget(self) -> None:
        text = "one two three four five six seven eight nine ten eleven twelve"
        prefix, _ = truncate_to_sentence_prefix(text, max_words=5)
        assert prefix.endswith("…")
