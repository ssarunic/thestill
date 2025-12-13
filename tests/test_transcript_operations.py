# Copyright 2025 thestill.ai
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

"""
Tests for Transcript manipulation operations.

These tests cover:
- adjust_timestamps(): Shift all timestamps by an offset
- apply_speaker_mapping(): Remap speaker labels
- merge(): Combine two transcripts handling overlap deduplication
- get_words_in_range(): Extract words within a time range
- build_speaker_mapping(): Build speaker mapping from overlap analysis
"""

import pytest

from thestill.models.transcript import Segment, Transcript, Word

# =============================================================================
# Fixtures for creating test transcripts
# =============================================================================


def make_word(text: str, start: float, end: float, speaker: str = None, probability: float = None) -> Word:
    """Helper to create a Word object."""
    return Word(word=text, start=start, end=end, speaker=speaker, probability=probability)


def make_segment(
    id: int,
    start: float,
    end: float,
    text: str,
    words: list[Word],
    speaker: str = None,
) -> Segment:
    """Helper to create a Segment object."""
    return Segment(id=id, start=start, end=end, text=text, words=words, speaker=speaker)


def make_transcript(
    segments: list[Segment],
    audio_file: str = "test.wav",
    language: str = "en-US",
    model_used: str = "test-model",
    processing_time: float = 1.0,
    timestamp: float = 1000000.0,
    diarization_enabled: bool = True,
    speakers_detected: int = None,
) -> Transcript:
    """Helper to create a Transcript object."""
    text = " ".join(seg.text for seg in segments)
    return Transcript(
        audio_file=audio_file,
        language=language,
        text=text,
        segments=segments,
        processing_time=processing_time,
        model_used=model_used,
        timestamp=timestamp,
        diarization_enabled=diarization_enabled,
        speakers_detected=speakers_detected,
    )


@pytest.fixture
def simple_transcript() -> Transcript:
    """A simple transcript with 2 segments, 2 speakers."""
    segments = [
        make_segment(
            id=0,
            start=0.0,
            end=2.5,
            text="Hello world",
            speaker="SPEAKER_00",
            words=[
                make_word("Hello", 0.0, 1.0, "SPEAKER_00"),
                make_word("world", 1.5, 2.5, "SPEAKER_00"),
            ],
        ),
        make_segment(
            id=1,
            start=3.0,
            end=5.0,
            text="How are you",
            speaker="SPEAKER_01",
            words=[
                make_word("How", 3.0, 3.5, "SPEAKER_01"),
                make_word("are", 3.6, 4.0, "SPEAKER_01"),
                make_word("you", 4.2, 5.0, "SPEAKER_01"),
            ],
        ),
    ]
    return make_transcript(segments, speakers_detected=2)


@pytest.fixture
def chunk1_transcript() -> Transcript:
    """First chunk transcript (0-12 minutes, timestamps 0-720 seconds)."""
    segments = [
        make_segment(
            id=0,
            start=0.0,
            end=5.0,
            text="Welcome to the show",
            speaker="SPEAKER_00",
            words=[
                make_word("Welcome", 0.0, 1.0, "SPEAKER_00"),
                make_word("to", 1.1, 1.5, "SPEAKER_00"),
                make_word("the", 1.6, 2.0, "SPEAKER_00"),
                make_word("show", 2.1, 5.0, "SPEAKER_00"),
            ],
        ),
        make_segment(
            id=1,
            start=660.0,  # 11 minutes - in overlap region
            end=720.0,  # 12 minutes
            text="This is exciting",
            speaker="SPEAKER_01",
            words=[
                make_word("This", 660.0, 662.0, "SPEAKER_01"),
                make_word("is", 663.0, 665.0, "SPEAKER_01"),
                make_word("exciting", 666.0, 720.0, "SPEAKER_01"),
            ],
        ),
    ]
    return make_transcript(segments, speakers_detected=2)


@pytest.fixture
def chunk2_transcript() -> Transcript:
    """
    Second chunk transcript (11-23 minutes, timestamps 660-1380 seconds).

    Note: In Google's chunked transcription, each chunk is transcribed independently.
    The overlap region (660-720s) appears in both chunks. Speaker labels are assigned
    independently, so SPEAKER_01 in chunk1 might be SPEAKER_00 in chunk2.
    """
    segments = [
        make_segment(
            id=0,
            start=660.0,  # 11 minutes - overlap region start
            end=720.0,  # 12 minutes - overlap region end
            text="This is exciting",
            speaker="SPEAKER_00",  # Different label than chunk1!
            words=[
                make_word("This", 660.0, 662.0, "SPEAKER_00"),
                make_word("is", 663.0, 665.0, "SPEAKER_00"),
                make_word("exciting", 666.0, 720.0, "SPEAKER_00"),
            ],
        ),
        make_segment(
            id=1,
            start=800.0,
            end=900.0,
            text="Tell me more",
            speaker="SPEAKER_01",  # This is actually SPEAKER_00 from chunk1
            words=[
                make_word("Tell", 800.0, 830.0, "SPEAKER_01"),
                make_word("me", 835.0, 860.0, "SPEAKER_01"),
                make_word("more", 865.0, 900.0, "SPEAKER_01"),
            ],
        ),
    ]
    return make_transcript(segments, speakers_detected=2)


# =============================================================================
# Tests for adjust_timestamps()
# =============================================================================


class TestAdjustTimestamps:
    """Tests for Transcript.adjust_timestamps() method."""

    def test_adjust_positive_offset(self, simple_transcript: Transcript):
        """Shifting timestamps forward by positive offset."""
        offset = 100.0
        adjusted = simple_transcript.adjust_timestamps(offset)

        # Check segment timestamps
        assert adjusted.segments[0].start == 100.0
        assert adjusted.segments[0].end == 102.5
        assert adjusted.segments[1].start == 103.0
        assert adjusted.segments[1].end == 105.0

        # Check word timestamps
        assert adjusted.segments[0].words[0].start == 100.0
        assert adjusted.segments[0].words[0].end == 101.0
        assert adjusted.segments[0].words[1].start == 101.5
        assert adjusted.segments[0].words[1].end == 102.5

        assert adjusted.segments[1].words[0].start == 103.0
        assert adjusted.segments[1].words[2].end == 105.0

    def test_adjust_negative_offset(self, simple_transcript: Transcript):
        """Shifting timestamps backward by negative offset (e.g., trimming start)."""
        # First shift forward so we can shift back without going negative
        shifted = simple_transcript.adjust_timestamps(10.0)
        adjusted = shifted.adjust_timestamps(-5.0)

        assert adjusted.segments[0].start == 5.0
        assert adjusted.segments[0].end == 7.5

    def test_adjust_zero_offset(self, simple_transcript: Transcript):
        """Zero offset should not change timestamps."""
        adjusted = simple_transcript.adjust_timestamps(0.0)

        assert adjusted.segments[0].start == 0.0
        assert adjusted.segments[0].end == 2.5
        assert adjusted.segments[0].words[0].start == 0.0

    def test_adjust_preserves_other_fields(self, simple_transcript: Transcript):
        """Adjustment should preserve non-timestamp fields."""
        adjusted = simple_transcript.adjust_timestamps(100.0)

        assert adjusted.audio_file == simple_transcript.audio_file
        assert adjusted.language == simple_transcript.language
        assert adjusted.model_used == simple_transcript.model_used
        assert adjusted.diarization_enabled == simple_transcript.diarization_enabled
        assert adjusted.speakers_detected == simple_transcript.speakers_detected

        # Text content preserved
        assert adjusted.segments[0].text == "Hello world"
        assert adjusted.segments[0].speaker == "SPEAKER_00"
        assert adjusted.segments[0].words[0].word == "Hello"

    def test_adjust_returns_new_transcript(self, simple_transcript: Transcript):
        """Adjustment should return a new Transcript, not modify in place."""
        original_start = simple_transcript.segments[0].start
        adjusted = simple_transcript.adjust_timestamps(100.0)

        # Original unchanged
        assert simple_transcript.segments[0].start == original_start
        # New transcript has adjusted values
        assert adjusted.segments[0].start == original_start + 100.0

    def test_adjust_handles_empty_transcript(self):
        """Empty transcript should be handled gracefully."""
        transcript = make_transcript(segments=[])
        adjusted = transcript.adjust_timestamps(100.0)

        assert adjusted.segments == []

    def test_adjust_handles_words_without_timestamps(self):
        """Words with None timestamps should be handled."""
        segments = [
            make_segment(
                id=0,
                start=0.0,
                end=2.0,
                text="Hello",
                words=[Word(word="Hello", start=None, end=None)],
            ),
        ]
        transcript = make_transcript(segments)
        adjusted = transcript.adjust_timestamps(100.0)

        # Segment adjusted
        assert adjusted.segments[0].start == 100.0
        # Word timestamps remain None
        assert adjusted.segments[0].words[0].start is None
        assert adjusted.segments[0].words[0].end is None

    def test_adjust_with_fractional_offset(self, simple_transcript: Transcript):
        """Fractional offsets should work correctly."""
        adjusted = simple_transcript.adjust_timestamps(0.123)

        assert adjusted.segments[0].start == pytest.approx(0.123, rel=1e-6)
        assert adjusted.segments[0].words[0].start == pytest.approx(0.123, rel=1e-6)


# =============================================================================
# Tests for apply_speaker_mapping()
# =============================================================================


class TestApplySpeakerMapping:
    """Tests for Transcript.apply_speaker_mapping() method."""

    def test_apply_simple_mapping(self, simple_transcript: Transcript):
        """Apply a simple speaker rename."""
        mapping = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        remapped = simple_transcript.apply_speaker_mapping(mapping)

        assert remapped.segments[0].speaker == "Alice"
        assert remapped.segments[1].speaker == "Bob"

        # Words also remapped
        assert remapped.segments[0].words[0].speaker == "Alice"
        assert remapped.segments[1].words[0].speaker == "Bob"

    def test_apply_partial_mapping(self, simple_transcript: Transcript):
        """Mapping that only covers some speakers."""
        mapping = {"SPEAKER_00": "Alice"}  # SPEAKER_01 not in mapping
        remapped = simple_transcript.apply_speaker_mapping(mapping)

        assert remapped.segments[0].speaker == "Alice"
        assert remapped.segments[1].speaker == "SPEAKER_01"  # Unchanged

    def test_apply_empty_mapping(self, simple_transcript: Transcript):
        """Empty mapping should not change anything."""
        remapped = simple_transcript.apply_speaker_mapping({})

        assert remapped.segments[0].speaker == "SPEAKER_00"
        assert remapped.segments[1].speaker == "SPEAKER_01"

    def test_apply_mapping_returns_new_transcript(self, simple_transcript: Transcript):
        """Mapping should return a new Transcript, not modify in place."""
        mapping = {"SPEAKER_00": "Alice"}
        remapped = simple_transcript.apply_speaker_mapping(mapping)

        # Original unchanged
        assert simple_transcript.segments[0].speaker == "SPEAKER_00"
        # New transcript has mapped values
        assert remapped.segments[0].speaker == "Alice"

    def test_apply_mapping_preserves_other_fields(self, simple_transcript: Transcript):
        """Mapping should preserve non-speaker fields."""
        mapping = {"SPEAKER_00": "Alice"}
        remapped = simple_transcript.apply_speaker_mapping(mapping)

        assert remapped.segments[0].text == "Hello world"
        assert remapped.segments[0].start == 0.0
        assert remapped.segments[0].words[0].word == "Hello"

    def test_apply_mapping_handles_none_speaker(self):
        """Segments/words with None speaker should be unaffected."""
        segments = [
            make_segment(
                id=0,
                start=0.0,
                end=2.0,
                text="Hello",
                speaker=None,
                words=[make_word("Hello", 0.0, 2.0, speaker=None)],
            ),
        ]
        transcript = make_transcript(segments)
        remapped = transcript.apply_speaker_mapping({"SPEAKER_00": "Alice"})

        assert remapped.segments[0].speaker is None
        assert remapped.segments[0].words[0].speaker is None

    def test_apply_mapping_speaker_swap(self, simple_transcript: Transcript):
        """Swap speaker labels (reconciliation scenario)."""
        # In chunk 2, SPEAKER_00 is actually SPEAKER_01 from chunk 1
        mapping = {"SPEAKER_00": "SPEAKER_01", "SPEAKER_01": "SPEAKER_00"}
        remapped = simple_transcript.apply_speaker_mapping(mapping)

        assert remapped.segments[0].speaker == "SPEAKER_01"
        assert remapped.segments[1].speaker == "SPEAKER_00"


# =============================================================================
# Tests for get_words_in_range()
# =============================================================================


class TestGetWordsInRange:
    """Tests for Transcript.get_words_in_range() method."""

    def test_get_words_full_range(self, simple_transcript: Transcript):
        """Get all words when range covers entire transcript."""
        words = simple_transcript.get_words_in_range(0.0, 10.0)

        assert len(words) == 5
        assert words[0].word == "Hello"
        assert words[-1].word == "you"

    def test_get_words_partial_range(self, simple_transcript: Transcript):
        """Get words in a partial range."""
        words = simple_transcript.get_words_in_range(3.0, 4.5)

        # Should get "How", "are" (starts at 3.0, 3.6)
        # "you" starts at 4.2, which is within range
        assert len(words) == 3
        word_texts = [w.word for w in words]
        assert "How" in word_texts
        assert "are" in word_texts
        assert "you" in word_texts

    def test_get_words_empty_range(self, simple_transcript: Transcript):
        """Range with no words returns empty list."""
        words = simple_transcript.get_words_in_range(2.6, 2.9)

        assert words == []

    def test_get_words_before_transcript(self, simple_transcript: Transcript):
        """Range before transcript starts returns empty list."""
        words = simple_transcript.get_words_in_range(-10.0, -1.0)

        assert words == []

    def test_get_words_after_transcript(self, simple_transcript: Transcript):
        """Range after transcript ends returns empty list."""
        words = simple_transcript.get_words_in_range(100.0, 200.0)

        assert words == []

    def test_get_words_boundary_inclusive(self, simple_transcript: Transcript):
        """Range boundaries should be inclusive."""
        # Word "Hello" starts at exactly 0.0
        words = simple_transcript.get_words_in_range(0.0, 0.5)

        assert len(words) == 1
        assert words[0].word == "Hello"

    def test_get_words_preserves_speaker(self, simple_transcript: Transcript):
        """Returned words should have speaker info preserved."""
        words = simple_transcript.get_words_in_range(0.0, 10.0)

        assert words[0].speaker == "SPEAKER_00"
        assert words[2].speaker == "SPEAKER_01"

    def test_get_words_empty_transcript(self):
        """Empty transcript returns empty list."""
        transcript = make_transcript(segments=[])
        words = transcript.get_words_in_range(0.0, 10.0)

        assert words == []


# =============================================================================
# Tests for build_speaker_mapping()
# =============================================================================


class TestBuildSpeakerMapping:
    """Tests for Transcript.build_speaker_mapping() method."""

    def test_build_mapping_from_overlap(self, chunk1_transcript: Transcript, chunk2_transcript: Transcript):
        """Build speaker mapping from overlapping region."""
        # Overlap region: 660-720 seconds
        # chunk1 has SPEAKER_01 saying "This is exciting"
        # chunk2 has SPEAKER_00 saying "This is exciting"
        # Mapping should be: SPEAKER_00 (chunk2) -> SPEAKER_01 (chunk1)
        mapping = chunk2_transcript.build_speaker_mapping(
            other=chunk1_transcript,
            overlap_start=660.0,
            overlap_end=720.0,
            match_window_sec=0.5,
            min_votes=2,
        )

        assert mapping.get("SPEAKER_00") == "SPEAKER_01"

    def test_build_mapping_no_overlap(self, simple_transcript: Transcript):
        """No overlap region should return empty mapping."""
        other = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=100.0,
                    end=110.0,
                    text="Different content",
                    speaker="SPEAKER_00",
                    words=[make_word("Different", 100.0, 105.0, "SPEAKER_00")],
                ),
            ]
        )

        mapping = simple_transcript.build_speaker_mapping(
            other=other,
            overlap_start=50.0,
            overlap_end=60.0,  # No words in this range
        )

        assert mapping == {}

    def test_build_mapping_insufficient_votes(self):
        """Mapping requires minimum vote threshold."""
        # Create transcripts with only 1 matching word (below threshold)
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )
        chunk2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_01",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_01")],
                ),
            ]
        )

        mapping = chunk2.build_speaker_mapping(
            other=chunk1,
            overlap_start=0.0,
            overlap_end=10.0,
            min_votes=3,  # Requires 3 matching words
        )

        assert mapping == {}  # Only 1 match, below threshold

    def test_build_mapping_majority_voting(self):
        """Multiple speakers should use majority voting."""
        # chunk1: SPEAKER_00 says words A, B, C; SPEAKER_01 says words D, E
        # chunk2: SPEAKER_X says words A, B, C; SPEAKER_Y says words D, E
        # Mapping should be: X->00, Y->01
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=3.0,
                    text="one two three",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("one", 0.0, 1.0, "SPEAKER_00"),
                        make_word("two", 1.0, 2.0, "SPEAKER_00"),
                        make_word("three", 2.0, 3.0, "SPEAKER_00"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=3.0,
                    end=5.0,
                    text="four five",
                    speaker="SPEAKER_01",
                    words=[
                        make_word("four", 3.0, 4.0, "SPEAKER_01"),
                        make_word("five", 4.0, 5.0, "SPEAKER_01"),
                    ],
                ),
            ]
        )
        chunk2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=3.0,
                    text="one two three",
                    speaker="SPEAKER_X",
                    words=[
                        make_word("one", 0.0, 1.0, "SPEAKER_X"),
                        make_word("two", 1.0, 2.0, "SPEAKER_X"),
                        make_word("three", 2.0, 3.0, "SPEAKER_X"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=3.0,
                    end=5.0,
                    text="four five",
                    speaker="SPEAKER_Y",
                    words=[
                        make_word("four", 3.0, 4.0, "SPEAKER_Y"),
                        make_word("five", 4.0, 5.0, "SPEAKER_Y"),
                    ],
                ),
            ]
        )

        mapping = chunk2.build_speaker_mapping(
            other=chunk1,
            overlap_start=0.0,
            overlap_end=10.0,
            min_votes=2,
        )

        assert mapping.get("SPEAKER_X") == "SPEAKER_00"
        assert mapping.get("SPEAKER_Y") == "SPEAKER_01"

    def test_build_mapping_case_insensitive(self):
        """Word matching should be case-insensitive."""
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=3.0,
                    text="Hello World Test",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("Hello", 0.0, 1.0, "SPEAKER_00"),
                        make_word("World", 1.0, 2.0, "SPEAKER_00"),
                        make_word("Test", 2.0, 3.0, "SPEAKER_00"),
                    ],
                ),
            ]
        )
        chunk2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=3.0,
                    text="hello world test",
                    speaker="SPEAKER_01",
                    words=[
                        make_word("hello", 0.0, 1.0, "SPEAKER_01"),
                        make_word("world", 1.0, 2.0, "SPEAKER_01"),
                        make_word("test", 2.0, 3.0, "SPEAKER_01"),
                    ],
                ),
            ]
        )

        mapping = chunk2.build_speaker_mapping(
            other=chunk1,
            overlap_start=0.0,
            overlap_end=10.0,
            min_votes=3,
        )

        assert mapping.get("SPEAKER_01") == "SPEAKER_00"


# =============================================================================
# Tests for merge()
# =============================================================================


class TestMerge:
    """Tests for Transcript.merge() method."""

    def test_merge_non_overlapping(self):
        """Merge two non-overlapping transcripts."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello world",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("Hello", 0.0, 2.0, "SPEAKER_00"),
                        make_word("world", 3.0, 5.0, "SPEAKER_00"),
                    ],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=10.0,
                    end=15.0,
                    text="How are you",
                    speaker="SPEAKER_01",
                    words=[
                        make_word("How", 10.0, 11.0, "SPEAKER_01"),
                        make_word("are", 12.0, 13.0, "SPEAKER_01"),
                        make_word("you", 14.0, 15.0, "SPEAKER_01"),
                    ],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        # Should have all words from both
        all_words = []
        for seg in merged.segments:
            all_words.extend(seg.words)

        assert len(all_words) == 5
        word_texts = [w.word for w in all_words]
        assert "Hello" in word_texts
        assert "world" in word_texts
        assert "How" in word_texts
        assert "are" in word_texts
        assert "you" in word_texts

    def test_merge_with_overlap_deduplication(self):
        """Merge transcripts with overlapping region - duplicates removed."""
        # Chunk 1: "Hello world" at 0-5s, overlap region has "test" at 8-10s
        # Chunk 2: overlap region has "test" at 8-10s, then "goodbye" at 12-15s
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello world",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("Hello", 0.0, 2.0, "SPEAKER_00"),
                        make_word("world", 3.0, 5.0, "SPEAKER_00"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=8.0,
                    end=10.0,
                    text="test",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("test", 8.0, 10.0, "SPEAKER_00"),
                    ],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=8.0,
                    end=10.0,
                    text="test",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("test", 8.0, 10.0, "SPEAKER_00"),  # Duplicate!
                    ],
                ),
                make_segment(
                    id=1,
                    start=12.0,
                    end=15.0,
                    text="goodbye",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("goodbye", 12.0, 15.0, "SPEAKER_00"),
                    ],
                ),
            ]
        )

        merged = transcript1.merge(transcript2, duplicate_window_sec=0.5)

        # Should deduplicate "test" - only 4 unique words
        all_words = []
        for seg in merged.segments:
            all_words.extend(seg.words)

        word_texts = [w.word for w in all_words]
        assert word_texts.count("test") == 1  # Deduplicated
        assert len(word_texts) == 4  # Hello, world, test, goodbye

    def test_merge_preserves_speaker_labels(self):
        """Merged transcript should preserve speaker labels."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=10.0,
                    end=15.0,
                    text="World",
                    speaker="SPEAKER_01",
                    words=[make_word("World", 10.0, 15.0, "SPEAKER_01")],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        speakers = merged.get_speakers()
        assert "SPEAKER_00" in speakers
        assert "SPEAKER_01" in speakers

    def test_merge_rebuilds_segments_by_speaker(self):
        """Merged segments should be grouped by speaker changes."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="one two",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("one", 0.0, 2.0, "SPEAKER_00"),
                        make_word("two", 3.0, 5.0, "SPEAKER_00"),
                    ],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=6.0,
                    end=8.0,
                    text="three",
                    speaker="SPEAKER_00",  # Same speaker continues
                    words=[make_word("three", 6.0, 8.0, "SPEAKER_00")],
                ),
                make_segment(
                    id=1,
                    start=9.0,
                    end=11.0,
                    text="four",
                    speaker="SPEAKER_01",  # Speaker change
                    words=[make_word("four", 9.0, 11.0, "SPEAKER_01")],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        # Should have 2 segments: SPEAKER_00 (one, two, three), SPEAKER_01 (four)
        assert len(merged.segments) == 2
        assert merged.segments[0].speaker == "SPEAKER_00"
        assert "one" in merged.segments[0].text
        assert "two" in merged.segments[0].text
        assert "three" in merged.segments[0].text
        assert merged.segments[1].speaker == "SPEAKER_01"
        assert "four" in merged.segments[1].text

    def test_merge_sorted_by_timestamp(self):
        """Merged words should be sorted by timestamp."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=10.0,
                    end=15.0,
                    text="second",
                    speaker="SPEAKER_00",
                    words=[make_word("second", 10.0, 15.0, "SPEAKER_00")],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="first",
                    speaker="SPEAKER_00",
                    words=[make_word("first", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        all_words = []
        for seg in merged.segments:
            all_words.extend(seg.words)

        assert all_words[0].word == "first"
        assert all_words[1].word == "second"

    def test_merge_empty_with_non_empty(self):
        """Merging empty transcript with non-empty should return non-empty."""
        empty = make_transcript(segments=[])
        non_empty = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )

        merged = empty.merge(non_empty)

        assert len(merged.segments) == 1
        assert merged.segments[0].text == "Hello"

    def test_merge_both_empty(self):
        """Merging two empty transcripts should return empty."""
        empty1 = make_transcript(segments=[])
        empty2 = make_transcript(segments=[])

        merged = empty1.merge(empty2)

        assert merged.segments == []

    def test_merge_updates_text(self):
        """Merged transcript text should be concatenated from segments."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=10.0,
                    end=15.0,
                    text="World",
                    speaker="SPEAKER_00",
                    words=[make_word("World", 10.0, 15.0, "SPEAKER_00")],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        assert "Hello" in merged.text
        assert "World" in merged.text

    def test_merge_renumbers_segment_ids(self):
        """Merged segments should have sequential IDs starting from 0."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=5,  # Non-zero ID
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ]
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=10,  # Non-zero ID
                    start=10.0,
                    end=15.0,
                    text="World",
                    speaker="SPEAKER_01",
                    words=[make_word("World", 10.0, 15.0, "SPEAKER_01")],
                ),
            ]
        )

        merged = transcript1.merge(transcript2)

        assert merged.segments[0].id == 0
        assert merged.segments[1].id == 1

    def test_merge_preserves_first_transcript_metadata(self):
        """Merged transcript should use metadata from first transcript."""
        transcript1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="Hello",
                    speaker="SPEAKER_00",
                    words=[make_word("Hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ],
            audio_file="original.wav",
            language="en-US",
            model_used="whisper",
        )
        transcript2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=10.0,
                    end=15.0,
                    text="World",
                    speaker="SPEAKER_00",
                    words=[make_word("World", 10.0, 15.0, "SPEAKER_00")],
                ),
            ],
            audio_file="chunk2.wav",
            language="fr-FR",
            model_used="google",
        )

        merged = transcript1.merge(transcript2)

        assert merged.audio_file == "original.wav"
        assert merged.language == "en-US"
        assert merged.model_used == "whisper"
