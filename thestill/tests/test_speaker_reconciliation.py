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
Unit tests for speaker reconciliation across chunks in GoogleCloudTranscriber.

Tests the algorithm that reconciles speaker labels (SPEAKER_00, SPEAKER_01, etc.)
across audio chunks, since Google Speech-to-Text assigns labels independently per chunk.
"""

from unittest.mock import MagicMock, patch

import pytest

# We need to mock Google Cloud dependencies before importing the transcriber
with patch.dict(
    "sys.modules",
    {
        "google.api_core.client_options": MagicMock(),
        "google.cloud": MagicMock(),
        "google.cloud.storage": MagicMock(),
        "google.cloud.speech_v2": MagicMock(),
        "google.cloud.speech_v2.types": MagicMock(),
        "google.oauth2": MagicMock(),
        "google.oauth2.service_account": MagicMock(),
    },
):
    # Patch GOOGLE_CLOUD_AVAILABLE to True
    import thestill.core.google_transcriber as gt_module

    gt_module.GOOGLE_CLOUD_AVAILABLE = True
    from thestill.core.google_transcriber import GoogleCloudTranscriber


@pytest.fixture
def transcriber():
    """Create a GoogleCloudTranscriber with mocked clients."""
    with patch.object(GoogleCloudTranscriber, "_initialize_clients"):
        transcriber = GoogleCloudTranscriber(
            project_id="test-project",
            credentials_path=None,
            enable_diarization=True,
        )
        transcriber.speech_client = MagicMock()
        transcriber.storage_client = MagicMock()
        return transcriber


def make_word(word: str, start: float, end: float, speaker: str) -> dict:
    """Helper to create a word dict."""
    return {
        "word": word,
        "start": start,
        "end": end,
        "probability": 0.95,
        "speaker": speaker,
    }


def make_segment(segment_id: int, words: list, speaker: str) -> dict:
    """Helper to create a segment dict from words."""
    return {
        "id": segment_id,
        "start": words[0]["start"] if words else 0,
        "end": words[-1]["end"] if words else 0,
        "text": " ".join(w["word"] for w in words),
        "speaker": speaker,
        "words": words,
    }


def make_transcript(segments: list) -> dict:
    """Helper to create a transcript dict."""
    return {
        "segments": segments,
        "text": " ".join(s["text"] for s in segments),
    }


class TestGetWordsInRange:
    """Tests for _get_words_in_range method."""

    def test_extracts_words_within_range(self, transcriber):
        """Should extract only words within the specified time range."""
        words = [
            make_word("hello", 0.0, 0.5, "SPEAKER_00"),
            make_word("world", 1.0, 1.5, "SPEAKER_00"),
            make_word("how", 2.0, 2.5, "SPEAKER_01"),
            make_word("are", 3.0, 3.5, "SPEAKER_01"),
            make_word("you", 4.0, 4.5, "SPEAKER_00"),
        ]
        segment = make_segment(0, words, "SPEAKER_00")
        transcript = make_transcript([segment])

        result = transcriber._get_words_in_range(transcript, 1.0, 3.5)

        assert len(result) == 3
        assert result[0]["word"] == "world"
        assert result[1]["word"] == "how"
        assert result[2]["word"] == "are"

    def test_returns_empty_for_no_match(self, transcriber):
        """Should return empty list if no words in range."""
        words = [make_word("hello", 0.0, 0.5, "SPEAKER_00")]
        segment = make_segment(0, words, "SPEAKER_00")
        transcript = make_transcript([segment])

        result = transcriber._get_words_in_range(transcript, 10.0, 20.0)

        assert result == []

    def test_handles_empty_transcript(self, transcriber):
        """Should handle transcript with no segments."""
        transcript = {"segments": []}

        result = transcriber._get_words_in_range(transcript, 0.0, 10.0)

        assert result == []


class TestBuildSpeakerMappingFromOverlap:
    """Tests for _build_speaker_mapping_from_overlap method."""

    def test_builds_mapping_from_matching_words(self, transcriber):
        """Should build correct mapping when words match between chunks."""
        # Previous chunk: SPEAKER_00 says first 4 words, SPEAKER_01 says last 4 words
        prev_words = [
            make_word("hello", 60.0, 60.5, "SPEAKER_00"),
            make_word("world", 60.6, 61.0, "SPEAKER_00"),
            make_word("this", 61.1, 61.4, "SPEAKER_00"),
            make_word("is", 61.5, 61.8, "SPEAKER_00"),
            make_word("how", 62.0, 62.3, "SPEAKER_01"),
            make_word("are", 62.4, 62.7, "SPEAKER_01"),
            make_word("you", 62.8, 63.0, "SPEAKER_01"),
            make_word("today", 63.1, 63.4, "SPEAKER_01"),
        ]
        prev_segment = make_segment(0, prev_words, "SPEAKER_00")
        prev_transcript = make_transcript([prev_segment])

        # Current chunk: speakers swapped - SPEAKER_01 says first 4, SPEAKER_00 says last 4
        curr_words = [
            make_word("hello", 60.0, 60.5, "SPEAKER_01"),  # Was SPEAKER_00
            make_word("world", 60.6, 61.0, "SPEAKER_01"),
            make_word("this", 61.1, 61.4, "SPEAKER_01"),
            make_word("is", 61.5, 61.8, "SPEAKER_01"),
            make_word("how", 62.0, 62.3, "SPEAKER_00"),  # Was SPEAKER_01
            make_word("are", 62.4, 62.7, "SPEAKER_00"),
            make_word("you", 62.8, 63.0, "SPEAKER_00"),
            make_word("today", 63.1, 63.4, "SPEAKER_00"),
        ]
        curr_segment = make_segment(0, curr_words, "SPEAKER_01")
        curr_transcript = make_transcript([curr_segment])

        mapping = transcriber._build_speaker_mapping_from_overlap(prev_transcript, curr_transcript, 60.0, 64.0)

        # SPEAKER_01 in current should map to SPEAKER_00 in previous (4 votes)
        # SPEAKER_00 in current should map to SPEAKER_01 in previous (4 votes)
        assert mapping.get("SPEAKER_01") == "SPEAKER_00"
        assert mapping.get("SPEAKER_00") == "SPEAKER_01"

    def test_returns_empty_when_no_words_in_overlap(self, transcriber):
        """Should return empty mapping if no words in overlap region."""
        prev_words = [make_word("hello", 0.0, 0.5, "SPEAKER_00")]
        prev_transcript = make_transcript([make_segment(0, prev_words, "SPEAKER_00")])

        curr_words = [make_word("world", 100.0, 100.5, "SPEAKER_01")]
        curr_transcript = make_transcript([make_segment(0, curr_words, "SPEAKER_01")])

        mapping = transcriber._build_speaker_mapping_from_overlap(prev_transcript, curr_transcript, 60.0, 70.0)

        assert mapping == {}

    def test_requires_minimum_votes(self, transcriber):
        """Should not create mapping with too few matching words."""
        # Only 2 matching words - below MIN_SPEAKER_VOTES threshold (3)
        prev_words = [
            make_word("hello", 60.0, 60.5, "SPEAKER_00"),
            make_word("world", 60.6, 61.0, "SPEAKER_00"),
        ]
        prev_transcript = make_transcript([make_segment(0, prev_words, "SPEAKER_00")])

        curr_words = [
            make_word("hello", 60.0, 60.5, "SPEAKER_01"),
            make_word("world", 60.6, 61.0, "SPEAKER_01"),
        ]
        curr_transcript = make_transcript([make_segment(0, curr_words, "SPEAKER_01")])

        mapping = transcriber._build_speaker_mapping_from_overlap(prev_transcript, curr_transcript, 60.0, 65.0)

        # Should not map SPEAKER_01 because only 2 votes (below threshold of 3)
        assert "SPEAKER_01" not in mapping

    def test_handles_timestamp_tolerance(self, transcriber):
        """Should match words within timestamp tolerance window."""
        # Words with slightly different timestamps (within 500ms tolerance)
        prev_words = [
            make_word("hello", 60.0, 60.5, "SPEAKER_00"),
            make_word("world", 60.6, 61.0, "SPEAKER_00"),
            make_word("test", 61.2, 61.6, "SPEAKER_00"),
        ]
        prev_transcript = make_transcript([make_segment(0, prev_words, "SPEAKER_00")])

        # Same words but timestamps differ by ~0.3s
        curr_words = [
            make_word("hello", 60.3, 60.8, "SPEAKER_01"),
            make_word("world", 60.9, 61.3, "SPEAKER_01"),
            make_word("test", 61.5, 61.9, "SPEAKER_01"),
        ]
        curr_transcript = make_transcript([make_segment(0, curr_words, "SPEAKER_01")])

        mapping = transcriber._build_speaker_mapping_from_overlap(prev_transcript, curr_transcript, 60.0, 62.0)

        assert mapping.get("SPEAKER_01") == "SPEAKER_00"


class TestApplySpeakerMappingToTranscript:
    """Tests for _apply_speaker_mapping_to_transcript method."""

    def test_remaps_segment_and_word_speakers(self, transcriber):
        """Should remap speaker IDs in both segments and words."""
        words = [
            make_word("hello", 0.0, 0.5, "SPEAKER_01"),
            make_word("world", 0.6, 1.0, "SPEAKER_01"),
        ]
        segment = make_segment(0, words, "SPEAKER_01")
        transcript = make_transcript([segment])

        mapping = {"SPEAKER_01": "SPEAKER_00"}
        transcriber._apply_speaker_mapping_to_transcript(transcript, mapping)

        assert transcript["segments"][0]["speaker"] == "SPEAKER_00"
        assert transcript["segments"][0]["words"][0]["speaker"] == "SPEAKER_00"
        assert transcript["segments"][0]["words"][1]["speaker"] == "SPEAKER_00"

    def test_handles_partial_mapping(self, transcriber):
        """Should only remap speakers that are in the mapping."""
        words1 = [make_word("hello", 0.0, 0.5, "SPEAKER_01")]
        words2 = [make_word("world", 1.0, 1.5, "SPEAKER_02")]
        segment1 = make_segment(0, words1, "SPEAKER_01")
        segment2 = make_segment(1, words2, "SPEAKER_02")
        transcript = make_transcript([segment1, segment2])

        # Only map SPEAKER_01, leave SPEAKER_02 unchanged
        mapping = {"SPEAKER_01": "SPEAKER_00"}
        transcriber._apply_speaker_mapping_to_transcript(transcript, mapping)

        assert transcript["segments"][0]["speaker"] == "SPEAKER_00"
        assert transcript["segments"][1]["speaker"] == "SPEAKER_02"  # Unchanged

    def test_handles_empty_mapping(self, transcriber):
        """Should not modify transcript with empty mapping."""
        words = [make_word("hello", 0.0, 0.5, "SPEAKER_01")]
        segment = make_segment(0, words, "SPEAKER_01")
        transcript = make_transcript([segment])

        transcriber._apply_speaker_mapping_to_transcript(transcript, {})

        assert transcript["segments"][0]["speaker"] == "SPEAKER_01"


class TestReconcileSpeakersAcrossChunks:
    """Tests for _reconcile_speakers_across_chunks method."""

    def test_single_chunk_unchanged(self, transcriber):
        """Should return single chunk unchanged."""
        words = [make_word("hello", 0.0, 0.5, "SPEAKER_00")]
        segment = make_segment(0, words, "SPEAKER_00")
        transcript = make_transcript([segment])

        chunks = [{"transcript": transcript, "start_ms": 0, "end_ms": 60000}]
        result = transcriber._reconcile_speakers_across_chunks(chunks)

        assert result == chunks
        assert result[0]["transcript"]["segments"][0]["speaker"] == "SPEAKER_00"

    def test_reconciles_two_chunks_with_swapped_speakers(self, transcriber):
        """Should reconcile speaker labels when they're swapped between chunks."""
        # Chunk 1: SPEAKER_00 is the host (minutes 0-9, overlap 8-9)
        chunk1_words = [
            make_word("welcome", 0.0, 0.5, "SPEAKER_00"),
            make_word("to", 0.6, 0.8, "SPEAKER_00"),
            make_word("the", 0.9, 1.1, "SPEAKER_00"),
            make_word("show", 1.2, 1.5, "SPEAKER_00"),
            # Overlap region words (at ~8 minutes = 480s)
            make_word("interesting", 480.0, 480.5, "SPEAKER_00"),
            make_word("point", 480.6, 481.0, "SPEAKER_00"),
            make_word("about", 481.1, 481.4, "SPEAKER_00"),
        ]
        chunk1_segment = make_segment(0, chunk1_words, "SPEAKER_00")
        chunk1_transcript = make_transcript([chunk1_segment])

        # Chunk 2: Same words in overlap but SPEAKER_01 (speakers swapped!)
        # Chunk 2 starts at 8 minutes (480s), overlap is 480-540
        chunk2_words = [
            # Overlap region - same words but different speaker ID
            make_word("interesting", 480.0, 480.5, "SPEAKER_01"),  # Was SPEAKER_00
            make_word("point", 480.6, 481.0, "SPEAKER_01"),
            make_word("about", 481.1, 481.4, "SPEAKER_01"),
            # Rest of chunk 2
            make_word("technology", 540.0, 540.5, "SPEAKER_01"),
            make_word("today", 540.6, 541.0, "SPEAKER_01"),
        ]
        chunk2_segment = make_segment(0, chunk2_words, "SPEAKER_01")
        chunk2_transcript = make_transcript([chunk2_segment])

        chunks = [
            {"transcript": chunk1_transcript, "start_ms": 0, "end_ms": 540000},  # 0-9 min
            {"transcript": chunk2_transcript, "start_ms": 480000, "end_ms": 1020000},  # 8-17 min
        ]

        result = transcriber._reconcile_speakers_across_chunks(chunks)

        # Chunk 2's SPEAKER_01 should now be SPEAKER_00 (mapped from chunk 1)
        chunk2_result = result[1]["transcript"]
        assert chunk2_result["segments"][0]["speaker"] == "SPEAKER_00"
        for word in chunk2_result["segments"][0]["words"]:
            assert word["speaker"] == "SPEAKER_00"

    def test_handles_no_overlap(self, transcriber):
        """Should not modify chunks when there's no overlap."""
        chunk1_words = [make_word("hello", 0.0, 0.5, "SPEAKER_00")]
        chunk1_transcript = make_transcript([make_segment(0, chunk1_words, "SPEAKER_00")])

        chunk2_words = [make_word("world", 100.0, 100.5, "SPEAKER_01")]
        chunk2_transcript = make_transcript([make_segment(0, chunk2_words, "SPEAKER_01")])

        # No overlap: chunk1 ends at 60s, chunk2 starts at 100s
        chunks = [
            {"transcript": chunk1_transcript, "start_ms": 0, "end_ms": 60000},
            {"transcript": chunk2_transcript, "start_ms": 100000, "end_ms": 160000},
        ]

        result = transcriber._reconcile_speakers_across_chunks(chunks)

        # Speakers should remain unchanged (no overlap to reconcile from)
        assert result[0]["transcript"]["segments"][0]["speaker"] == "SPEAKER_00"
        assert result[1]["transcript"]["segments"][0]["speaker"] == "SPEAKER_01"

    def test_reconciles_three_chunks(self, transcriber):
        """Should reconcile speakers across multiple chunks sequentially."""

        # Create words for overlap regions
        def make_overlap_words(base_time: float, speaker: str) -> list:
            """Create 5 words at base_time with given speaker."""
            return [
                make_word("the", base_time, base_time + 0.3, speaker),
                make_word("quick", base_time + 0.4, base_time + 0.7, speaker),
                make_word("brown", base_time + 0.8, base_time + 1.1, speaker),
                make_word("fox", base_time + 1.2, base_time + 1.4, speaker),
                make_word("jumps", base_time + 1.5, base_time + 1.8, speaker),
            ]

        # Chunk 1: SPEAKER_00 at end (overlap 480-540s)
        chunk1_words = make_overlap_words(480.0, "SPEAKER_00")
        chunk1_transcript = make_transcript([make_segment(0, chunk1_words, "SPEAKER_00")])

        # Chunk 2: SPEAKER_01 at start (same person, different label), then SPEAKER_02 at end
        chunk2_overlap_start = make_overlap_words(480.0, "SPEAKER_01")  # Maps to SPEAKER_00
        chunk2_overlap_end = make_overlap_words(960.0, "SPEAKER_02")  # New speaker in chunk2
        chunk2_words = chunk2_overlap_start + chunk2_overlap_end
        chunk2_segment = make_segment(0, chunk2_words, "SPEAKER_01")
        chunk2_segment["words"] = chunk2_words  # Include all words
        chunk2_transcript = make_transcript([chunk2_segment])

        # Chunk 3: SPEAKER_03 at start (should map to SPEAKER_02 from chunk2)
        chunk3_words = make_overlap_words(960.0, "SPEAKER_03")
        chunk3_transcript = make_transcript([make_segment(0, chunk3_words, "SPEAKER_03")])

        chunks = [
            {"transcript": chunk1_transcript, "start_ms": 0, "end_ms": 540000},
            {"transcript": chunk2_transcript, "start_ms": 480000, "end_ms": 1020000},
            {"transcript": chunk3_transcript, "start_ms": 960000, "end_ms": 1500000},
        ]

        result = transcriber._reconcile_speakers_across_chunks(chunks)

        # Chunk 1 unchanged
        assert result[0]["transcript"]["segments"][0]["speaker"] == "SPEAKER_00"

        # Chunk 2: SPEAKER_01 should map to SPEAKER_00
        # (SPEAKER_02 might not map if insufficient votes in this test setup)
        chunk2_first_word = result[1]["transcript"]["segments"][0]["words"][0]
        assert chunk2_first_word["speaker"] == "SPEAKER_00"

        # Chunk 3: SPEAKER_03 should map to SPEAKER_02 (from chunk 2's end)
        chunk3_first_word = result[2]["transcript"]["segments"][0]["words"][0]
        assert chunk3_first_word["speaker"] == "SPEAKER_02"


class TestMergeChunkTranscriptsWithReconciliation:
    """Integration tests for _merge_chunk_transcripts with reconciliation."""

    def test_merge_reconciles_before_deduplication(self, transcriber):
        """Should reconcile speakers before merging and deduplicating."""
        # Two chunks with swapped speaker labels in overlap
        chunk1_words = [
            make_word("hello", 0.0, 0.5, "SPEAKER_00"),
            make_word("world", 0.6, 1.0, "SPEAKER_00"),
            # Overlap region
            make_word("this", 8.0, 8.3, "SPEAKER_00"),
            make_word("is", 8.4, 8.6, "SPEAKER_00"),
            make_word("a", 8.7, 8.9, "SPEAKER_00"),
            make_word("test", 9.0, 9.3, "SPEAKER_00"),
        ]
        chunk1_segment = make_segment(0, chunk1_words, "SPEAKER_00")
        chunk1_transcript = make_transcript([chunk1_segment])

        chunk2_words = [
            # Overlap region (swapped speaker)
            make_word("this", 8.0, 8.3, "SPEAKER_01"),
            make_word("is", 8.4, 8.6, "SPEAKER_01"),
            make_word("a", 8.7, 8.9, "SPEAKER_01"),
            make_word("test", 9.0, 9.3, "SPEAKER_01"),
            # New content
            make_word("for", 10.0, 10.3, "SPEAKER_01"),
            make_word("you", 10.4, 10.7, "SPEAKER_01"),
        ]
        chunk2_segment = make_segment(0, chunk2_words, "SPEAKER_01")
        chunk2_transcript = make_transcript([chunk2_segment])

        chunks = [
            {"transcript": chunk1_transcript, "start_ms": 0, "end_ms": 9500},
            {"transcript": chunk2_transcript, "start_ms": 8000, "end_ms": 18000},
        ]

        result = transcriber._merge_chunk_transcripts(chunks, "/test.wav", "en-US")

        # All segments should have SPEAKER_00 (reconciled)
        for segment in result["segments"]:
            assert segment["speaker"] == "SPEAKER_00"

        # Check that overlap was deduplicated (no duplicate "this is a test")
        all_words = [w["word"] for s in result["segments"] for w in s.get("words", [])]
        # Should have: hello, world, this, is, a, test, for, you
        assert all_words.count("this") == 1
        assert all_words.count("test") == 1
