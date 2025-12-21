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
Integration tests for Transcript operations that simulate real chunked transcription.

These tests verify that the typed Transcript methods produce equivalent results
to what the old dict-based implementation would have produced.
"""

import pytest

from thestill.models.transcript import Segment, Transcript, Word


def make_word(text: str, start: float, end: float, speaker: str = None) -> Word:
    """Helper to create a Word object."""
    return Word(word=text, start=start, end=end, speaker=speaker)


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
    model_used: str = "google-cloud-speech-v2-chirp_3",
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


class TestRealisticChunkMerging:
    """Tests that simulate real Google Cloud chunked transcription scenarios."""

    def test_two_chunk_podcast_with_speaker_reconciliation(self):
        """
        Simulate a real podcast transcription split into 2 chunks with 1-minute overlap.

        Scenario:
        - Chunk 1: 0-12 minutes (720 seconds)
        - Chunk 2: 11-23 minutes (660-1380 seconds)
        - Overlap: 11-12 minutes (660-720 seconds)

        In the overlap, Host speaks. But Google assigns different speaker IDs:
        - Chunk 1: SPEAKER_00 = Host, SPEAKER_01 = Guest
        - Chunk 2: SPEAKER_01 = Host, SPEAKER_00 = Guest (swapped!)

        The speaker reconciliation should detect this and remap chunk 2.
        """
        # Chunk 1: Host (SPEAKER_00) and Guest (SPEAKER_01)
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=10.0,
                    text="Welcome to the show I am your host",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("Welcome", 0.0, 1.5, "SPEAKER_00"),
                        make_word("to", 1.6, 2.0, "SPEAKER_00"),
                        make_word("the", 2.1, 2.5, "SPEAKER_00"),
                        make_word("show", 2.6, 3.5, "SPEAKER_00"),
                        make_word("I", 4.0, 4.5, "SPEAKER_00"),
                        make_word("am", 4.6, 5.0, "SPEAKER_00"),
                        make_word("your", 5.1, 5.5, "SPEAKER_00"),
                        make_word("host", 5.6, 10.0, "SPEAKER_00"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=300.0,
                    end=350.0,
                    text="Thanks for having me",
                    speaker="SPEAKER_01",
                    words=[
                        make_word("Thanks", 300.0, 310.0, "SPEAKER_01"),
                        make_word("for", 311.0, 320.0, "SPEAKER_01"),
                        make_word("having", 321.0, 340.0, "SPEAKER_01"),
                        make_word("me", 341.0, 350.0, "SPEAKER_01"),
                    ],
                ),
                # Overlap region: 660-720s - Host speaks about the topic
                make_segment(
                    id=2,
                    start=660.0,
                    end=720.0,
                    text="This topic is really fascinating lets explore it",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("This", 660.0, 665.0, "SPEAKER_00"),
                        make_word("topic", 666.0, 670.0, "SPEAKER_00"),
                        make_word("is", 671.0, 673.0, "SPEAKER_00"),
                        make_word("really", 674.0, 678.0, "SPEAKER_00"),
                        make_word("fascinating", 679.0, 690.0, "SPEAKER_00"),
                        make_word("lets", 691.0, 700.0, "SPEAKER_00"),
                        make_word("explore", 701.0, 710.0, "SPEAKER_00"),
                        make_word("it", 711.0, 720.0, "SPEAKER_00"),
                    ],
                ),
            ],
            speakers_detected=2,
        )

        # Chunk 2: Speakers are SWAPPED!
        # SPEAKER_01 = Host (was SPEAKER_00 in chunk1)
        # SPEAKER_00 = Guest (was SPEAKER_01 in chunk1)
        chunk2 = make_transcript(
            segments=[
                # Overlap region: same content, different speaker IDs
                make_segment(
                    id=0,
                    start=660.0,
                    end=720.0,
                    text="This topic is really fascinating lets explore it",
                    speaker="SPEAKER_01",  # Swapped! Was SPEAKER_00 in chunk1
                    words=[
                        make_word("This", 660.0, 665.0, "SPEAKER_01"),
                        make_word("topic", 666.0, 670.0, "SPEAKER_01"),
                        make_word("is", 671.0, 673.0, "SPEAKER_01"),
                        make_word("really", 674.0, 678.0, "SPEAKER_01"),
                        make_word("fascinating", 679.0, 690.0, "SPEAKER_01"),
                        make_word("lets", 691.0, 700.0, "SPEAKER_01"),
                        make_word("explore", 701.0, 710.0, "SPEAKER_01"),
                        make_word("it", 711.0, 720.0, "SPEAKER_01"),
                    ],
                ),
                # After overlap: Guest responds
                make_segment(
                    id=1,
                    start=800.0,
                    end=900.0,
                    text="Absolutely I think we should start with history",
                    speaker="SPEAKER_00",  # Swapped! This is actually SPEAKER_01 (Guest)
                    words=[
                        make_word("Absolutely", 800.0, 820.0, "SPEAKER_00"),
                        make_word("I", 821.0, 830.0, "SPEAKER_00"),
                        make_word("think", 831.0, 850.0, "SPEAKER_00"),
                        make_word("we", 851.0, 860.0, "SPEAKER_00"),
                        make_word("should", 861.0, 870.0, "SPEAKER_00"),
                        make_word("start", 871.0, 880.0, "SPEAKER_00"),
                        make_word("with", 881.0, 890.0, "SPEAKER_00"),
                        make_word("history", 891.0, 900.0, "SPEAKER_00"),
                    ],
                ),
            ],
            speakers_detected=2,
        )

        # Step 1: Build speaker mapping from overlap
        # Chunk2's overlap words should map to chunk1's overlap words
        speaker_mapping = chunk2.build_speaker_mapping(
            other=chunk1,
            overlap_start=660.0,
            overlap_end=720.0,
            match_window_sec=10.0,  # Generous window for test
            min_votes=3,
        )

        # Verify: SPEAKER_01 in chunk2 should map to SPEAKER_00 in chunk1 (Host)
        assert (
            speaker_mapping.get("SPEAKER_01") == "SPEAKER_00"
        ), f"Expected SPEAKER_01->SPEAKER_00, got {speaker_mapping}"

        # Step 2: Apply mapping to chunk2
        chunk2_remapped = chunk2.apply_speaker_mapping(speaker_mapping)

        # Verify remapping worked
        # The overlap segment should now have SPEAKER_00 (Host)
        assert chunk2_remapped.segments[0].speaker == "SPEAKER_00"
        # All words in overlap should be SPEAKER_00
        for word in chunk2_remapped.segments[0].words:
            assert word.speaker == "SPEAKER_00"

        # Step 3: Merge the transcripts
        merged = chunk1.merge(chunk2_remapped)

        # Verify merged transcript properties
        assert len(merged.get_speakers()) == 2, "Should have 2 speakers"

        # The overlap words should be deduplicated
        all_words = []
        for seg in merged.segments:
            all_words.extend(seg.words)

        # Count "fascinating" - should appear only once (deduplicated)
        fascinating_count = sum(1 for w in all_words if w.word == "fascinating")
        assert fascinating_count == 1, f"'fascinating' should appear once, got {fascinating_count}"

        # Verify total text includes content from both chunks
        assert "Welcome" in merged.text
        assert "Thanks" in merged.text
        assert "fascinating" in merged.text
        assert "history" in merged.text

    def test_three_chunk_podcast_progressive_merging(self):
        """
        Simulate a longer podcast split into 3 chunks, merged progressively.

        This tests the approach used in _transcribe_chunked where we:
        1. Start with chunk1
        2. Reconcile and merge chunk2
        3. Reconcile chunk3 against merged result and merge
        """
        # Simplified chunks with clear overlap patterns
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="one two three",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("one", 0.0, 1.0, "SPEAKER_00"),
                        make_word("two", 2.0, 3.0, "SPEAKER_00"),
                        make_word("three", 4.0, 5.0, "SPEAKER_00"),
                    ],
                ),
                # Overlap region with chunk2: 8-12s
                make_segment(
                    id=1,
                    start=8.0,
                    end=12.0,
                    text="overlap one overlap two",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("overlap", 8.0, 9.0, "SPEAKER_00"),
                        make_word("one", 9.5, 10.0, "SPEAKER_00"),
                        make_word("overlap", 10.5, 11.0, "SPEAKER_00"),
                        make_word("two", 11.5, 12.0, "SPEAKER_00"),
                    ],
                ),
            ],
        )

        chunk2 = make_transcript(
            segments=[
                # Overlap with chunk1: 8-12s (same speaker, no remapping needed)
                make_segment(
                    id=0,
                    start=8.0,
                    end=12.0,
                    text="overlap one overlap two",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("overlap", 8.0, 9.0, "SPEAKER_00"),
                        make_word("one", 9.5, 10.0, "SPEAKER_00"),
                        make_word("overlap", 10.5, 11.0, "SPEAKER_00"),
                        make_word("two", 11.5, 12.0, "SPEAKER_00"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=15.0,
                    end=18.0,
                    text="four five six",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("four", 15.0, 16.0, "SPEAKER_00"),
                        make_word("five", 16.5, 17.0, "SPEAKER_00"),
                        make_word("six", 17.5, 18.0, "SPEAKER_00"),
                    ],
                ),
                # Overlap with chunk3: 20-24s
                make_segment(
                    id=2,
                    start=20.0,
                    end=24.0,
                    text="overlap three overlap four",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("overlap", 20.0, 21.0, "SPEAKER_00"),
                        make_word("three", 21.5, 22.0, "SPEAKER_00"),
                        make_word("overlap", 22.5, 23.0, "SPEAKER_00"),
                        make_word("four", 23.5, 24.0, "SPEAKER_00"),
                    ],
                ),
            ],
        )

        chunk3 = make_transcript(
            segments=[
                # Overlap with chunk2: 20-24s
                make_segment(
                    id=0,
                    start=20.0,
                    end=24.0,
                    text="overlap three overlap four",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("overlap", 20.0, 21.0, "SPEAKER_00"),
                        make_word("three", 21.5, 22.0, "SPEAKER_00"),
                        make_word("overlap", 22.5, 23.0, "SPEAKER_00"),
                        make_word("four", 23.5, 24.0, "SPEAKER_00"),
                    ],
                ),
                make_segment(
                    id=1,
                    start=27.0,
                    end=30.0,
                    text="seven eight nine",
                    speaker="SPEAKER_00",
                    words=[
                        make_word("seven", 27.0, 28.0, "SPEAKER_00"),
                        make_word("eight", 28.5, 29.0, "SPEAKER_00"),
                        make_word("nine", 29.5, 30.0, "SPEAKER_00"),
                    ],
                ),
            ],
        )

        # Progressive merging (simulating _transcribe_chunked logic)
        merged = chunk1

        # Merge chunk2 (overlap 8-12s)
        mapping2 = chunk2.build_speaker_mapping(merged, 8.0, 12.0, min_votes=2)
        chunk2_remapped = chunk2.apply_speaker_mapping(mapping2) if mapping2 else chunk2
        merged = merged.merge(chunk2_remapped)

        # Merge chunk3 (overlap 20-24s)
        mapping3 = chunk3.build_speaker_mapping(merged, 20.0, 24.0, min_votes=2)
        chunk3_remapped = chunk3.apply_speaker_mapping(mapping3) if mapping3 else chunk3
        merged = merged.merge(chunk3_remapped)

        # Verify final merged transcript
        all_words = []
        for seg in merged.segments:
            all_words.extend(seg.words)

        # Should have unique words: one, two, three, overlap (x2 deduplicated), one, two,
        # four, five, six, overlap (x2 deduplicated), three, four, seven, eight, nine
        word_texts = [w.word for w in all_words]

        # Check key words are present
        assert "one" in word_texts
        assert "two" in word_texts
        assert "three" in word_texts
        assert "four" in word_texts
        assert "five" in word_texts
        assert "six" in word_texts
        assert "seven" in word_texts
        assert "eight" in word_texts
        assert "nine" in word_texts

        # Check no excessive duplication of overlap words
        # "overlap" appears in regions but should be deduplicated in each overlap
        overlap_count = word_texts.count("overlap")
        # We have 2 overlap regions, each with 2 "overlap" words, but they should be deduplicated
        # So we expect roughly 2 "overlap" words (one from each region after dedup)
        assert overlap_count <= 4, f"Too many 'overlap' words: {overlap_count}"

    def test_timestamp_adjustment_precision(self):
        """Test that timestamp adjustment maintains floating point precision."""
        transcript = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.001,
                    end=1.999,
                    text="precise",
                    speaker="SPEAKER_00",
                    words=[make_word("precise", 0.001, 1.999, "SPEAKER_00")],
                ),
            ],
        )

        # Adjust by fractional offset
        offset = 123.456789
        adjusted = transcript.adjust_timestamps(offset)

        assert adjusted.segments[0].start == pytest.approx(0.001 + offset, rel=1e-9)
        assert adjusted.segments[0].end == pytest.approx(1.999 + offset, rel=1e-9)
        assert adjusted.segments[0].words[0].start == pytest.approx(0.001 + offset, rel=1e-9)
        assert adjusted.segments[0].words[0].end == pytest.approx(1.999 + offset, rel=1e-9)

    def test_empty_overlap_no_mapping(self):
        """When there's no overlap, speaker mapping should return empty dict."""
        chunk1 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=0.0,
                    end=5.0,
                    text="hello",
                    speaker="SPEAKER_00",
                    words=[make_word("hello", 0.0, 5.0, "SPEAKER_00")],
                ),
            ],
        )

        chunk2 = make_transcript(
            segments=[
                make_segment(
                    id=0,
                    start=100.0,  # No overlap with chunk1
                    end=105.0,
                    text="world",
                    speaker="SPEAKER_01",
                    words=[make_word("world", 100.0, 105.0, "SPEAKER_01")],
                ),
            ],
        )

        # Overlap region that contains no words
        mapping = chunk2.build_speaker_mapping(chunk1, 50.0, 60.0)
        assert mapping == {}

        # Merging should still work (just concatenates)
        merged = chunk1.merge(chunk2)
        assert "hello" in merged.text
        assert "world" in merged.text
