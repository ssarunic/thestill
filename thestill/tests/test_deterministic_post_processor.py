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

"""Tests for deterministic transcript post-processing functions."""

import pytest

from thestill.core.transcript_cleaning_processor import TranscriptCleaningProcessor
from thestill.tests.conftest import MockLLMProvider


@pytest.fixture
def processor() -> TranscriptCleaningProcessor:
    """Create processor with mock provider for testing helper methods."""
    provider = MockLLMProvider()
    return TranscriptCleaningProcessor(provider=provider)


class TestApplySpeakerMapping:
    """Test speaker placeholder replacement."""

    def test_basic_replacement(self, processor: TranscriptCleaningProcessor) -> None:
        """Single speaker replacement."""
        transcript = "**SPEAKER_00:** Hello world."
        mapping = {"SPEAKER_00": "Scott Galloway"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert "**Scott Galloway:**" in result
        assert "SPEAKER_00" not in result

    def test_multiple_speakers(self, processor: TranscriptCleaningProcessor) -> None:
        """Multiple different speakers."""
        transcript = "**SPEAKER_00:** Hi.\n\n**SPEAKER_01:** Hello."
        mapping = {"SPEAKER_00": "Host", "SPEAKER_01": "Guest"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert "**Host:**" in result
        assert "**Guest:**" in result
        assert "SPEAKER_00" not in result
        assert "SPEAKER_01" not in result

    def test_unmapped_speaker_unchanged(self, processor: TranscriptCleaningProcessor) -> None:
        """Speakers not in mapping should remain unchanged."""
        transcript = "**SPEAKER_05:** Unknown speaker."
        mapping = {"SPEAKER_00": "Host"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert "**SPEAKER_05:**" in result

    def test_special_chars_in_name(self, processor: TranscriptCleaningProcessor) -> None:
        """Names with special characters (parentheses, periods)."""
        transcript = "**SPEAKER_00:** Test."
        mapping = {"SPEAKER_00": "Dr. Smith (PhD)"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert "**Dr. Smith (PhD):**" in result

    def test_empty_transcript(self, processor: TranscriptCleaningProcessor) -> None:
        """Empty transcript should return empty string."""
        result = processor._apply_speaker_mapping("", {"SPEAKER_00": "Host"})
        assert result == ""

    def test_empty_mapping(self, processor: TranscriptCleaningProcessor) -> None:
        """Empty mapping should return transcript unchanged."""
        transcript = "**SPEAKER_00:** Test."
        result = processor._apply_speaker_mapping(transcript, {})
        assert result == transcript

    def test_none_transcript(self, processor: TranscriptCleaningProcessor) -> None:
        """None transcript should return empty string."""
        result = processor._apply_speaker_mapping(None, {"SPEAKER_00": "Host"})  # type: ignore
        assert result == ""

    def test_none_mapping(self, processor: TranscriptCleaningProcessor) -> None:
        """None mapping should return transcript unchanged."""
        transcript = "**SPEAKER_00:** Test."
        result = processor._apply_speaker_mapping(transcript, None)  # type: ignore
        assert result == transcript

    def test_multiple_occurrences_same_speaker(self, processor: TranscriptCleaningProcessor) -> None:
        """Same speaker appearing multiple times."""
        transcript = "**SPEAKER_00:** First.\n\n**SPEAKER_00:** Second."
        mapping = {"SPEAKER_00": "Host"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert result.count("**Host:**") == 2
        assert "SPEAKER_00" not in result

    def test_empty_speaker_name_skipped(self, processor: TranscriptCleaningProcessor) -> None:
        """Empty speaker name should be skipped (placeholder remains)."""
        transcript = "**SPEAKER_00:** Test."
        mapping = {"SPEAKER_00": ""}

        result = processor._apply_speaker_mapping(transcript, mapping)

        assert "**SPEAKER_00:**" in result

    def test_speaker_in_dialogue_not_replaced(self, processor: TranscriptCleaningProcessor) -> None:
        """SPEAKER_XX in dialogue text (not bold prefix) should not be replaced."""
        transcript = "**SPEAKER_00:** I was talking to SPEAKER_01 yesterday."
        mapping = {"SPEAKER_00": "Host", "SPEAKER_01": "Guest"}

        result = processor._apply_speaker_mapping(transcript, mapping)

        # Bold prefix should be replaced
        assert "**Host:**" in result
        # Plain text mention should NOT be replaced (it's not **SPEAKER_01:**)
        assert "SPEAKER_01" in result


class TestApplyCorrections:
    """Test correction application with word boundaries."""

    def test_word_boundary_prevents_partial_match(self, processor: TranscriptCleaningProcessor) -> None:
        """Ensure 'La' -> 'LA' doesn't affect 'Language'."""
        transcript = "**Language:** en\n\nI went to La."
        corrections = [{"type": "spelling", "original": "La", "corrected": "LA"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "**Language:**" in result  # Not modified
        assert "I went to LA." in result  # Correctly replaced
        assert count == 1

    def test_word_boundary_with_punctuation(self, processor: TranscriptCleaningProcessor) -> None:
        """Word boundaries should work with adjacent punctuation."""
        transcript = "Visit La. It's great."
        corrections = [{"type": "spelling", "original": "La", "corrected": "LA"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "Visit LA." in result
        assert count == 1

    def test_spelling_multiple_occurrences(self, processor: TranscriptCleaningProcessor) -> None:
        """Spelling corrections should replace all occurrences."""
        transcript = "I use OpenAi and OpenAi is great."
        corrections = [{"type": "spelling", "original": "OpenAi", "corrected": "OpenAI"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert "OpenAi" not in result
        assert result.count("OpenAI") == 2
        # Count is 1 because we count corrections applied, not replacements
        assert count == 1

    def test_empty_corrections(self, processor: TranscriptCleaningProcessor) -> None:
        """Empty corrections list should return transcript unchanged."""
        transcript = "Hello world."
        corrections: list = []

        result, count = processor._apply_corrections(transcript, corrections)

        assert result == transcript
        assert count == 0

    def test_skip_empty_original(self, processor: TranscriptCleaningProcessor) -> None:
        """Corrections with empty original should be skipped."""
        transcript = "Hello world."
        corrections = [{"type": "spelling", "original": "", "corrected": "test"}]

        result, count = processor._apply_corrections(transcript, corrections)

        assert result == transcript
        assert count == 0


class TestGenerateCleanedTranscript:
    """Integration tests for the full deterministic Phase 3."""

    def test_full_pipeline(self, processor: TranscriptCleaningProcessor) -> None:
        """Test complete deterministic Phase 3 transformation."""
        corrected_markdown = """**SPEAKER_00:** Welcome to the show.

**SPEAKER_01:** Thanks for having me.

**SPEAKER_00:** Let's dive in."""

        speaker_mapping = {"SPEAKER_00": "Scott Galloway", "SPEAKER_01": "Guest"}

        result, chunks = processor._generate_cleaned_transcript(corrected_markdown, [], speaker_mapping, "Test Episode")

        # Verify speaker replacement
        assert "**Scott Galloway:**" in result
        assert "**Guest:**" in result
        assert "SPEAKER_00" not in result
        assert "SPEAKER_01" not in result

        # Verify no chunking (deterministic)
        assert chunks == 0

    def test_empty_speaker_mapping(self, processor: TranscriptCleaningProcessor) -> None:
        """Phase 3 with empty speaker mapping should return transcript unchanged."""
        transcript = "**SPEAKER_00:** Hello."

        result, chunks = processor._generate_cleaned_transcript(transcript, [], {}, "Test")

        # Speaker placeholder should remain
        assert "**SPEAKER_00:**" in result
        assert chunks == 0

    def test_preserves_formatting(self, processor: TranscriptCleaningProcessor) -> None:
        """Verify existing formatting is preserved."""
        transcript = """# Episode Title

**SPEAKER_00:** First paragraph.

**SPEAKER_00:** Second paragraph with **bold** text."""

        result, _ = processor._generate_cleaned_transcript(transcript, [], {"SPEAKER_00": "Host"}, "Test")

        # Heading preserved
        assert "# Episode Title" in result
        # Multiple paragraphs preserved
        assert "First paragraph." in result
        assert "Second paragraph" in result
        # Inline bold preserved
        assert "**bold**" in result
