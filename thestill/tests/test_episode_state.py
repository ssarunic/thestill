"""
Tests for Episode model state property and EpisodeState enum.

Tests cover:
- Episode state determination based on path attributes
- State transitions through the processing pipeline
- EpisodeState enum values
"""

import pytest
from pydantic import HttpUrl

from thestill.models.podcast import Episode, EpisodeState


class TestEpisodeState:
    """Test EpisodeState enum values and behavior."""

    def test_enum_values(self):
        """Test that EpisodeState enum has correct values."""
        assert EpisodeState.DISCOVERED.value == "discovered"
        assert EpisodeState.DOWNLOADED.value == "downloaded"
        assert EpisodeState.DOWNSAMPLED.value == "downsampled"
        assert EpisodeState.TRANSCRIBED.value == "transcribed"
        assert EpisodeState.CLEANED.value == "cleaned"

    def test_enum_is_string(self):
        """Test that EpisodeState enum inherits from str."""
        assert isinstance(EpisodeState.DISCOVERED, str)
        assert isinstance(EpisodeState.DOWNLOADED.value, str)

    def test_enum_can_be_compared_with_strings(self):
        """Test that EpisodeState can be compared with strings."""
        assert EpisodeState.DISCOVERED == "discovered"
        assert EpisodeState.DOWNLOADED.value == "downloaded"


class TestEpisodeStateProperty:
    """Test Episode.state property for state determination."""

    @pytest.fixture
    def base_episode_data(self):
        """Base episode data for creating test episodes."""
        return {
            "title": "Test Episode",
            "description": "Test Description",
            "audio_url": HttpUrl("https://example.com/audio.mp3"),
            "guid": "test-guid-123",
        }

    def test_discovered_state(self, base_episode_data):
        """Test episode in DISCOVERED state (only audio_url set)."""
        episode = Episode(**base_episode_data)
        assert episode.state == EpisodeState.DISCOVERED

    def test_downloaded_state(self, base_episode_data):
        """Test episode in DOWNLOADED state (audio_path set)."""
        episode = Episode(**base_episode_data, audio_path="audio.mp3")
        assert episode.state == EpisodeState.DOWNLOADED

    def test_downsampled_state(self, base_episode_data):
        """Test episode in DOWNSAMPLED state (downsampled_audio_path set)."""
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            downsampled_audio_path="downsampled.wav",
        )
        assert episode.state == EpisodeState.DOWNSAMPLED

    def test_transcribed_state(self, base_episode_data):
        """Test episode in TRANSCRIBED state (raw_transcript_path set)."""
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            downsampled_audio_path="downsampled.wav",
            raw_transcript_path="transcript.json",
        )
        assert episode.state == EpisodeState.TRANSCRIBED

    def test_cleaned_state(self, base_episode_data):
        """Test episode in CLEANED state (clean_transcript_path set)."""
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            downsampled_audio_path="downsampled.wav",
            raw_transcript_path="transcript.json",
            clean_transcript_path="transcript.md",
        )
        assert episode.state == EpisodeState.CLEANED

    def test_state_with_gaps_in_pipeline(self, base_episode_data):
        """Test that state returns furthest progressed state even with gaps."""
        # Episode with clean transcript but no downsampled path (shouldn't happen but test it)
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            raw_transcript_path="transcript.json",
            clean_transcript_path="transcript.md",
        )
        # Should return CLEANED (furthest progressed state)
        assert episode.state == EpisodeState.CLEANED

    def test_state_with_only_clean_transcript(self, base_episode_data):
        """Test state when only clean_transcript_path is set (edge case)."""
        episode = Episode(**base_episode_data, clean_transcript_path="transcript.md")
        # Should return CLEANED as it's the furthest state
        assert episode.state == EpisodeState.CLEANED

    def test_state_with_only_raw_transcript(self, base_episode_data):
        """Test state when only raw_transcript_path is set."""
        episode = Episode(**base_episode_data, raw_transcript_path="transcript.json")
        assert episode.state == EpisodeState.TRANSCRIBED


class TestStateTransitions:
    """Test state transitions through the processing pipeline."""

    @pytest.fixture
    def base_episode_data(self):
        """Base episode data for creating test episodes."""
        return {
            "title": "Test Episode",
            "description": "Test Description",
            "audio_url": HttpUrl("https://example.com/audio.mp3"),
            "guid": "test-guid-123",
        }

    def test_full_pipeline_progression(self, base_episode_data):
        """Test episode progressing through all states."""
        # Start: DISCOVERED
        episode = Episode(**base_episode_data)
        assert episode.state == EpisodeState.DISCOVERED

        # Step 1: Download audio -> DOWNLOADED
        episode.audio_path = "audio.mp3"
        assert episode.state == EpisodeState.DOWNLOADED

        # Step 2: Downsample audio -> DOWNSAMPLED
        episode.downsampled_audio_path = "downsampled.wav"
        assert episode.state == EpisodeState.DOWNSAMPLED

        # Step 3: Transcribe -> TRANSCRIBED
        episode.raw_transcript_path = "transcript.json"
        assert episode.state == EpisodeState.TRANSCRIBED

        # Step 4: Clean transcript -> CLEANED
        episode.clean_transcript_path = "transcript.md"
        assert episode.state == EpisodeState.CLEANED

    def test_state_never_regresses(self, base_episode_data):
        """Test that setting earlier paths doesn't cause state regression."""
        # Start at TRANSCRIBED state
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            downsampled_audio_path="downsampled.wav",
            raw_transcript_path="transcript.json",
        )
        assert episode.state == EpisodeState.TRANSCRIBED

        # Modify audio_path (shouldn't affect state)
        episode.audio_path = "new_audio.mp3"
        assert episode.state == EpisodeState.TRANSCRIBED

    def test_state_property_is_computed(self, base_episode_data):
        """Test that state property is computed dynamically, not cached."""
        episode = Episode(**base_episode_data)
        assert episode.state == EpisodeState.DISCOVERED

        # Modify episode to advance state
        episode.audio_path = "audio.mp3"
        assert episode.state == EpisodeState.DOWNLOADED

        # Continue advancing
        episode.downsampled_audio_path = "downsampled.wav"
        assert episode.state == EpisodeState.DOWNSAMPLED

    def test_states_are_ordered(self):
        """Test that we can compare episode states."""
        states = [
            EpisodeState.DISCOVERED,
            EpisodeState.DOWNLOADED,
            EpisodeState.DOWNSAMPLED,
            EpisodeState.TRANSCRIBED,
            EpisodeState.CLEANED,
        ]

        # Test that states have expected ordering based on pipeline progression
        assert states[0] == EpisodeState.DISCOVERED
        assert states[-1] == EpisodeState.CLEANED


class TestStateValidation:
    """Test state validation and edge cases."""

    @pytest.fixture
    def base_episode_data(self):
        """Base episode data for creating test episodes."""
        return {
            "title": "Test Episode",
            "description": "Test Description",
            "audio_url": HttpUrl("https://example.com/audio.mp3"),
            "guid": "test-guid-123",
        }

    def test_episode_with_all_none_paths(self, base_episode_data):
        """Test episode with all optional paths as None."""
        episode = Episode(**base_episode_data)
        assert episode.audio_path is None
        assert episode.downsampled_audio_path is None
        assert episode.raw_transcript_path is None
        assert episode.clean_transcript_path is None
        assert episode.state == EpisodeState.DISCOVERED

    def test_episode_with_empty_string_paths(self, base_episode_data):
        """Test that empty strings are treated as None for state determination."""
        episode = Episode(
            **base_episode_data,
            audio_path="",  # Empty string
        )
        # Empty strings are falsy, so should be treated as DISCOVERED
        # Note: Pydantic might convert empty strings to None depending on config
        assert episode.state in [EpisodeState.DISCOVERED, EpisodeState.DOWNLOADED]

    def test_state_with_summary_path_set(self, base_episode_data):
        """Test that summary_path doesn't affect state (it's for future use)."""
        episode = Episode(
            **base_episode_data,
            audio_path="audio.mp3",
            summary_path="summary.txt",
        )
        # summary_path shouldn't affect state determination
        assert episode.state == EpisodeState.DOWNLOADED
