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

"""Tests for :mod:`thestill.core.transcript_segmenter` (spec #18 Phase B)."""

from typing import List, Optional

import pytest

from thestill.core.transcript_segmenter import DegenerateTranscriptError, TranscriptSegmenter
from thestill.models.transcript import Segment, Transcript, Word


def _words(texts: List[str], start: float, *, step: float = 0.3) -> List[Word]:
    """Build a list of ``Word`` objects spaced ``step`` seconds apart.

    Each word's ``end`` is ``start + step - 0.05`` so there is a tiny
    intra-pair gap. This matters for the paragraph-gap detection tests.
    """
    out: List[Word] = []
    time = start
    for text in texts:
        out.append(Word(word=text, start=time, end=time + step - 0.05))
        time += step
    return out


def _segment(
    *,
    seg_id: int,
    start: float,
    end: float,
    text: str,
    speaker: Optional[str],
    words: Optional[List[Word]] = None,
) -> Segment:
    return Segment(
        id=seg_id,
        start=start,
        end=end,
        text=text,
        speaker=speaker,
        words=words or [],
    )


def _transcript(segments: List[Segment]) -> Transcript:
    return Transcript(
        audio_file="test.wav",
        language="en",
        text=" ".join(seg.text for seg in segments),
        segments=segments,
        processing_time=0.0,
        model_used="fixture",
        timestamp=0.0,
    )


class TestPauseGuardedMerge:
    """Consecutive same-speaker runs merge only under the pause ceiling."""

    def test_merges_short_same_speaker_fragments_within_pause_ceiling(self) -> None:
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=1.0,
                    text="hello world",
                    speaker="A",
                    words=_words(["hello", "world"], 0.0),
                ),
                _segment(
                    seg_id=1,
                    start=1.2,
                    end=2.5,
                    text="how are you",
                    speaker="A",
                    words=_words(["how", "are", "you"], 1.2),
                ),
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        assert len(result.segments) == 1
        merged = result.segments[0]
        assert merged.speaker == "A"
        assert merged.source_segment_ids == [0, 1]
        assert "hello world" in merged.text
        assert "how are you" in merged.text

    def test_refuses_to_merge_across_pause_above_ceiling(self) -> None:
        """A long silence between same-speaker segments preserves the boundary.

        This is the topic-shift / host-handoff case — even though the
        speaker label matches, the gap is semantically meaningful and the
        segmenter must not glue the two runs together.
        """
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=1.0,
                    text="hello",
                    speaker="A",
                    words=_words(["hello"], 0.0),
                ),
                _segment(
                    seg_id=1,
                    start=10.0,
                    end=11.0,
                    text="later thought",
                    speaker="A",
                    words=_words(["later", "thought"], 10.0),
                ),
            ]
        )

        result = TranscriptSegmenter(pause_ceiling_seconds=3.0).repair(transcript)

        assert len(result.segments) == 2
        assert result.segments[0].source_segment_ids == [0]
        assert result.segments[1].source_segment_ids == [1]

    def test_does_not_merge_across_speaker_change(self) -> None:
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=1.0,
                    text="hi there",
                    speaker="A",
                    words=_words(["hi", "there"], 0.0),
                ),
                _segment(
                    seg_id=1,
                    start=1.1,
                    end=2.0,
                    text="hi back",
                    speaker="B",
                    words=_words(["hi", "back"], 1.1),
                ),
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        assert len(result.segments) == 2
        assert result.segments[0].speaker == "A"
        assert result.segments[1].speaker == "B"


class TestLongRunSplitting:
    """Long same-speaker runs get split on paragraph / sentence boundaries."""

    def test_splits_long_run_at_paragraph_gap(self) -> None:
        """A 35-second pause in the word stream drives the split point."""
        first_half = _words(["word"] * 40, start=0.0, step=0.2)
        # 35 seconds after the last word's end — a huge paragraph gap.
        second_half = _words(["word"] * 40, start=45.0, step=0.2)

        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=9.0,
                    text=" ".join(["word"] * 40),
                    speaker="A",
                    words=first_half,
                ),
                _segment(
                    seg_id=1,
                    start=9.05,  # tight pause at the raw-segment level so merge happens
                    end=54.0,
                    text=" ".join(["word"] * 40),
                    speaker="A",
                    words=second_half,
                ),
            ]
        )

        result = TranscriptSegmenter(max_words_per_segment=60).repair(transcript)

        assert len(result.segments) == 2, "expected split at paragraph gap"
        assert len(result.segments[0].text.split()) == 40
        assert len(result.segments[1].text.split()) == 40

    def test_splits_long_run_at_sentence_boundary_when_no_paragraph_gap(self) -> None:
        """Falls back to sentence punctuation when there's no big silence."""
        # 80 words with a period at word 40; word gaps are all 0.3s.
        texts: List[str] = ["word"] * 80
        texts[39] = "word."
        words = _words(texts, start=0.0, step=0.3)
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=24.0,
                    text=" ".join(texts),
                    speaker="A",
                    words=words,
                ),
            ]
        )

        result = TranscriptSegmenter(max_words_per_segment=50, paragraph_gap_seconds=5.0).repair(transcript)

        assert len(result.segments) >= 2
        # Combined word count across splits equals the input (no words lost).
        combined = sum(len(seg.text.split()) for seg in result.segments)
        assert combined == 80

    def test_hard_cut_fallback_when_no_natural_boundaries(self) -> None:
        """Neither paragraph gaps nor sentence punctuation — forced cut."""
        texts = ["word"] * 100  # no punctuation, no pauses
        words = _words(texts, start=0.0, step=0.1)
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=10.0,
                    text=" ".join(texts),
                    speaker="A",
                    words=words,
                ),
            ]
        )

        result = TranscriptSegmenter(
            max_words_per_segment=40,
            paragraph_gap_seconds=5.0,
        ).repair(transcript)

        assert len(result.segments) == 3
        total_words = sum(len(seg.text.split()) for seg in result.segments)
        assert total_words == 100


class TestSourceAnchors:
    """Every cleaned segment carries a back-reference into the raw JSON."""

    def test_merged_run_lists_each_contributing_raw_id(self) -> None:
        transcript = _transcript(
            [
                _segment(
                    seg_id=i,
                    start=i * 0.5,
                    end=i * 0.5 + 0.4,
                    text=f"w{i}",
                    speaker="A",
                    words=_words([f"w{i}"], i * 0.5),
                )
                for i in range(5)
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        assert len(result.segments) == 1
        assert result.segments[0].source_segment_ids == [0, 1, 2, 3, 4]

    def test_word_span_points_into_the_raw_word_stream(self) -> None:
        transcript = _transcript(
            [
                _segment(
                    seg_id=7,
                    start=0.0,
                    end=2.0,
                    text="alpha beta gamma",
                    speaker="A",
                    words=_words(["alpha", "beta", "gamma"], 0.0),
                ),
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        span = result.segments[0].source_word_span
        assert span is not None
        assert span.start_segment_id == 7
        assert span.start_word_index == 0
        assert span.end_segment_id == 7
        assert span.end_word_index == 2

    def test_positional_ids_are_sequential_from_zero(self) -> None:
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=1.0, text="one", speaker="A", words=_words(["one"], 0.0)),
                _segment(seg_id=1, start=1.1, end=2.0, text="two", speaker="B", words=_words(["two"], 1.1)),
                _segment(seg_id=2, start=2.1, end=3.0, text="three", speaker="C", words=_words(["three"], 2.1)),
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        assert [s.id for s in result.segments] == [0, 1, 2]


class TestEdgeWordsMissingTimestamps:
    """Regression: segment anchors must stay tight when edge words have
    ``None`` timestamps. The capability gate only requires *some* word to
    carry timing; any specific chunk's first or last word may still be
    missing its ``start``/``end``. Falling back to ``0.0`` silently
    collapses the segment anchor and breaks the player's seek/highlight
    contract, so we walk inward and then defer to the raw segment's
    bounds as a last resort."""

    def test_first_word_missing_start_walks_inward(self) -> None:
        """Edge word has no ``start``; the next word provides it."""
        words = [
            Word(word="early", start=None, end=None),
            Word(word="middle", start=2.5, end=3.0),
            Word(word="late", start=3.0, end=3.5),
        ]
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=2.0,
                    end=4.0,
                    text="early middle late",
                    speaker="A",
                    words=words,
                )
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        # Chunk start should come from the first word with usable timing
        # (2.5), NOT from a hard 0.0 fallback and NOT from the raw
        # segment's 2.0 boundary (inner word wins over outer fallback).
        assert result.segments[0].start == 2.5
        assert result.segments[0].end == 3.5

    def test_last_word_missing_end_walks_inward(self) -> None:
        """Edge word has no ``end``; the previous word provides it."""
        words = [
            Word(word="start", start=1.0, end=1.5),
            Word(word="middle", start=1.5, end=2.0),
            Word(word="trailing", start=None, end=None),
        ]
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=3.0,
                    text="start middle trailing",
                    speaker="A",
                    words=words,
                )
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        assert result.segments[0].start == 1.0
        assert result.segments[0].end == 2.0

    def test_all_words_timingless_but_run_intact_falls_back_to_segment_bounds(
        self,
    ) -> None:
        """Chunk has no usable word timing at all — use raw-segment bounds."""
        # A mixed-timing transcript: one segment has word timestamps
        # (satisfies the capability gate), another does not and will be
        # exercised as a separate run thanks to the speaker change.
        timing_words = [Word(word="ok", start=0.0, end=1.0)]
        timingless_words = [
            Word(word="no", start=None, end=None),
            Word(word="timing", start=None, end=None),
        ]
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=1.0,
                    text="ok",
                    speaker="A",
                    words=timing_words,
                ),
                _segment(
                    seg_id=1,
                    start=10.0,
                    end=15.0,
                    text="no timing",
                    speaker="B",
                    words=timingless_words,
                ),
            ]
        )

        result = TranscriptSegmenter().repair(transcript)

        # Two runs (speaker change). The second run's words lack
        # timestamps, so the chunk falls back to the raw-segment bounds
        # — not a collapsed 0.0-anchored range.
        second = result.segments[1]
        assert second.speaker == "B"
        assert second.start == 10.0
        assert second.end == 15.0


class TestConstructorValidation:
    """Regression: invalid parameters must fail fast with a clear error."""

    def test_max_words_per_segment_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_words_per_segment"):
            TranscriptSegmenter(max_words_per_segment=0)

    def test_max_words_per_segment_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_words_per_segment"):
            TranscriptSegmenter(max_words_per_segment=-5)

    def test_pause_ceiling_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="pause_ceiling_seconds"):
            TranscriptSegmenter(pause_ceiling_seconds=-1.0)

    def test_paragraph_gap_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="paragraph_gap_seconds"):
            TranscriptSegmenter(paragraph_gap_seconds=-0.5)

    def test_zero_is_valid_for_pause_ceiling(self) -> None:
        """``0`` means "never merge across any gap" — legal, just strict."""
        TranscriptSegmenter(pause_ceiling_seconds=0.0)

    def test_one_is_valid_for_max_words(self) -> None:
        """Extreme-but-legal config: one word per segment."""
        TranscriptSegmenter(max_words_per_segment=1)


class TestDegenerateInputHandling:
    """The segmenter raises on inputs the routing layer should have filtered."""

    def test_raises_on_parakeet_style_stub(self) -> None:
        """Zero-length single segment with no words — the canonical stub."""
        transcript = _transcript(
            [
                _segment(seg_id=0, start=0.0, end=0.0, text="whole thing", speaker=None, words=[]),
            ]
        )

        with pytest.raises(DegenerateTranscriptError) as exc_info:
            TranscriptSegmenter().repair(transcript)

        assert exc_info.value.reason == "zero_length_segments"

    def test_raises_when_all_words_missing_timestamps(self) -> None:
        """Non-empty words but no usable timing still can't feed segmented cleanup."""
        words_without_timing = [
            Word(word="hello", start=None, end=None),
            Word(word="world", start=None, end=None),
        ]
        transcript = _transcript(
            [
                _segment(
                    seg_id=0,
                    start=0.0,
                    end=5.0,
                    text="hello world",
                    speaker="A",
                    words=words_without_timing,
                ),
            ]
        )

        with pytest.raises(DegenerateTranscriptError) as exc_info:
            TranscriptSegmenter().repair(transcript)

        assert exc_info.value.reason == "missing_word_timestamps"

    def test_raises_on_empty_segment_list(self) -> None:
        transcript = _transcript([])

        with pytest.raises(DegenerateTranscriptError) as exc_info:
            TranscriptSegmenter().repair(transcript)

        assert exc_info.value.reason == "no_segments"
