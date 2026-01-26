"""
Unit tests for DigestEpisodeSelector.

Tests the episode selection logic for digest processing with safety limits.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.repositories.digest_repository import DigestRepository
from thestill.repositories.podcast_repository import EpisodeRepository
from thestill.services.digest_selector import DigestEpisodeSelector, DigestSelectionCriteria, DigestSelectionResult


@pytest.fixture
def mock_repository():
    """Create mock episode repository."""
    return Mock(spec=EpisodeRepository)


@pytest.fixture
def mock_digest_repository():
    """Create mock digest repository."""
    return Mock(spec=DigestRepository)


@pytest.fixture
def sample_podcast():
    """Create a sample podcast for testing."""
    return Podcast(
        id="podcast-123",
        title="Test Podcast",
        description="A test podcast",
        rss_url="https://example.com/feed.xml",
    )


def make_episode(
    external_id: str,
    title: str,
    days_ago: int,
    state: EpisodeState,
    podcast_id: str = "podcast-123",
) -> Episode:
    """Helper to create episodes with specific states."""
    pub_date = datetime.now(timezone.utc) - timedelta(days=days_ago)

    # Set paths based on desired state
    episode = Episode(
        external_id=external_id,
        podcast_id=podcast_id,
        title=title,
        description=f"Description for {title}",
        pub_date=pub_date,
        audio_url=f"https://example.com/{external_id}.mp3",
    )

    # Set appropriate paths to achieve desired state
    if state == EpisodeState.DOWNLOADED:
        episode.audio_path = f"{external_id}.mp3"
    elif state == EpisodeState.DOWNSAMPLED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
    elif state == EpisodeState.TRANSCRIBED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
    elif state == EpisodeState.CLEANED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
        episode.clean_transcript_path = f"{external_id}.md"
    elif state == EpisodeState.SUMMARIZED:
        episode.audio_path = f"{external_id}.mp3"
        episode.downsampled_audio_path = f"{external_id}.wav"
        episode.raw_transcript_path = f"{external_id}.json"
        episode.clean_transcript_path = f"{external_id}.md"
        episode.summary_path = f"{external_id}_summary.md"
    elif state == EpisodeState.FAILED:
        episode.failed_at_stage = "download"
        episode.failure_reason = "Network error"

    return episode


class TestDigestSelectionCriteria:
    """Tests for DigestSelectionCriteria dataclass."""

    def test_default_values(self):
        """Test default criteria values."""
        criteria = DigestSelectionCriteria()
        assert criteria.since_days == 7
        assert criteria.max_episodes == 10
        assert criteria.podcast_id is None

    def test_custom_values(self):
        """Test custom criteria values."""
        criteria = DigestSelectionCriteria(
            since_days=14,
            max_episodes=25,
            podcast_id="abc-123",
        )
        assert criteria.since_days == 14
        assert criteria.max_episodes == 25
        assert criteria.podcast_id == "abc-123"

    def test_date_from_calculation(self):
        """Test that date_from is calculated correctly from since_days."""
        criteria = DigestSelectionCriteria(since_days=7)
        expected = datetime.now(timezone.utc) - timedelta(days=7)
        # Allow 1 second tolerance for test execution time
        diff = abs((criteria.date_from - expected).total_seconds())
        assert diff < 1


class TestDigestEpisodeSelector:
    """Tests for DigestEpisodeSelector."""

    def test_select_excludes_summarized_and_failed(self, mock_repository, sample_podcast):
        """Test that SUMMARIZED and FAILED states are excluded."""
        # Create episodes in various states
        discovered = make_episode("ep1", "Discovered", 1, EpisodeState.DISCOVERED)
        downloaded = make_episode("ep2", "Downloaded", 2, EpisodeState.DOWNLOADED)
        cleaned = make_episode("ep3", "Cleaned", 3, EpisodeState.CLEANED)
        summarized = make_episode("ep4", "Summarized", 4, EpisodeState.SUMMARIZED)
        failed = make_episode("ep5", "Failed", 5, EpisodeState.FAILED)

        mock_repository.get_all_episodes.return_value = (
            [
                (sample_podcast, discovered),
                (sample_podcast, downloaded),
                (sample_podcast, cleaned),
                (sample_podcast, summarized),
                (sample_podcast, failed),
            ],
            5,
        )

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria())

        # Should exclude SUMMARIZED and FAILED
        assert len(result.episodes) == 3
        states = [ep.state for _, ep in result.episodes]
        assert EpisodeState.DISCOVERED in states
        assert EpisodeState.DOWNLOADED in states
        assert EpisodeState.CLEANED in states
        assert EpisodeState.SUMMARIZED not in states
        assert EpisodeState.FAILED not in states

    def test_select_respects_max_episodes(self, mock_repository, sample_podcast):
        """Test that max_episodes limit is applied."""
        # Create 15 episodes
        episodes = [
            (sample_podcast, make_episode(f"ep{i}", f"Episode {i}", i, EpisodeState.DISCOVERED)) for i in range(15)
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 15)

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(max_episodes=5))

        assert len(result.episodes) == 5
        assert result.total_matching == 15

    def test_select_filters_by_podcast_id(self, mock_repository, sample_podcast):
        """Test that podcast_id filter is passed to repository."""
        mock_repository.get_all_episodes.return_value = ([], 0)

        selector = DigestEpisodeSelector(mock_repository)
        selector.select(DigestSelectionCriteria(podcast_id="specific-podcast-id"))

        # Verify podcast_id was passed to repository
        call_kwargs = mock_repository.get_all_episodes.call_args.kwargs
        assert call_kwargs["podcast_id"] == "specific-podcast-id"

    def test_select_filters_by_date(self, mock_repository, sample_podcast):
        """Test that date_from filter is passed to repository."""
        mock_repository.get_all_episodes.return_value = ([], 0)

        selector = DigestEpisodeSelector(mock_repository)
        criteria = DigestSelectionCriteria(since_days=14)
        selector.select(criteria)

        # Verify date_from was passed to repository
        call_kwargs = mock_repository.get_all_episodes.call_args.kwargs
        assert call_kwargs["date_from"] is not None
        # Check it's approximately 14 days ago
        expected_date = datetime.now(timezone.utc) - timedelta(days=14)
        assert abs((call_kwargs["date_from"] - expected_date).total_seconds()) < 60

    def test_select_orders_by_newest_first(self, mock_repository, sample_podcast):
        """Test that episodes are ordered by pub_date descending."""
        mock_repository.get_all_episodes.return_value = ([], 0)

        selector = DigestEpisodeSelector(mock_repository)
        selector.select(DigestSelectionCriteria())

        call_kwargs = mock_repository.get_all_episodes.call_args.kwargs
        assert call_kwargs["sort_by"] == "pub_date"
        assert call_kwargs["sort_order"] == "desc"

    def test_select_returns_correct_result_structure(self, mock_repository, sample_podcast):
        """Test that DigestSelectionResult has correct structure."""
        episode = make_episode("ep1", "Test Episode", 1, EpisodeState.DISCOVERED)
        mock_repository.get_all_episodes.return_value = ([(sample_podcast, episode)], 1)

        selector = DigestEpisodeSelector(mock_repository)
        criteria = DigestSelectionCriteria(since_days=7, max_episodes=10)
        result = selector.select(criteria)

        assert isinstance(result, DigestSelectionResult)
        assert len(result.episodes) == 1
        assert result.total_matching == 1
        assert result.criteria == criteria
        assert result.criteria.since_days == 7
        assert result.criteria.max_episodes == 10

    def test_preview_returns_same_as_select(self, mock_repository, sample_podcast):
        """Test that preview() returns the same result as select()."""
        episode = make_episode("ep1", "Test", 1, EpisodeState.DISCOVERED)
        mock_repository.get_all_episodes.return_value = ([(sample_podcast, episode)], 1)

        selector = DigestEpisodeSelector(mock_repository)
        criteria = DigestSelectionCriteria()

        select_result = selector.select(criteria)
        preview_result = selector.preview(criteria)

        assert len(select_result.episodes) == len(preview_result.episodes)
        assert select_result.total_matching == preview_result.total_matching

    def test_all_non_terminal_states_included(self, mock_repository, sample_podcast):
        """Test that all states except SUMMARIZED and FAILED are selected."""
        # Create one episode per non-terminal state
        episodes = [
            (sample_podcast, make_episode("ep1", "Discovered", 1, EpisodeState.DISCOVERED)),
            (sample_podcast, make_episode("ep2", "Downloaded", 2, EpisodeState.DOWNLOADED)),
            (sample_podcast, make_episode("ep3", "Downsampled", 3, EpisodeState.DOWNSAMPLED)),
            (sample_podcast, make_episode("ep4", "Transcribed", 4, EpisodeState.TRANSCRIBED)),
            (sample_podcast, make_episode("ep5", "Cleaned", 5, EpisodeState.CLEANED)),
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 5)

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(max_episodes=100))

        assert len(result.episodes) == 5
        states = {ep.state for _, ep in result.episodes}
        expected_states = {
            EpisodeState.DISCOVERED,
            EpisodeState.DOWNLOADED,
            EpisodeState.DOWNSAMPLED,
            EpisodeState.TRANSCRIBED,
            EpisodeState.CLEANED,
        }
        assert states == expected_states

    def test_empty_result_when_no_matching_episodes(self, mock_repository):
        """Test handling of no matching episodes."""
        mock_repository.get_all_episodes.return_value = ([], 0)

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria())

        assert len(result.episodes) == 0
        assert result.total_matching == 0


class TestDigestEpisodeSelectorReadyOnly:
    """Tests for ready_only mode (THES-154)."""

    def test_ready_only_selects_only_summarized(self, mock_repository, sample_podcast):
        """Test that ready_only mode only selects SUMMARIZED episodes."""
        discovered = make_episode("ep1", "Discovered", 1, EpisodeState.DISCOVERED)
        downloaded = make_episode("ep2", "Downloaded", 2, EpisodeState.DOWNLOADED)
        summarized1 = make_episode("ep3", "Summarized 1", 3, EpisodeState.SUMMARIZED)
        summarized2 = make_episode("ep4", "Summarized 2", 4, EpisodeState.SUMMARIZED)
        failed = make_episode("ep5", "Failed", 5, EpisodeState.FAILED)

        mock_repository.get_all_episodes.return_value = (
            [
                (sample_podcast, discovered),
                (sample_podcast, downloaded),
                (sample_podcast, summarized1),
                (sample_podcast, summarized2),
                (sample_podcast, failed),
            ],
            5,
        )

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(ready_only=True))

        # Should only include SUMMARIZED episodes
        assert len(result.episodes) == 2
        states = [ep.state for _, ep in result.episodes]
        assert all(state == EpisodeState.SUMMARIZED for state in states)

    def test_ready_only_respects_max_episodes(self, mock_repository, sample_podcast):
        """Test that max_episodes limit applies in ready_only mode."""
        # Create 10 summarized episodes
        episodes = [
            (sample_podcast, make_episode(f"ep{i}", f"Episode {i}", i, EpisodeState.SUMMARIZED)) for i in range(10)
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 10)

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(ready_only=True, max_episodes=3))

        assert len(result.episodes) == 3
        assert result.total_matching == 10

    def test_ready_only_empty_when_no_summarized(self, mock_repository, sample_podcast):
        """Test that ready_only returns empty when no summarized episodes exist."""
        episodes = [
            (sample_podcast, make_episode("ep1", "Discovered", 1, EpisodeState.DISCOVERED)),
            (sample_podcast, make_episode("ep2", "Downloaded", 2, EpisodeState.DOWNLOADED)),
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 2)

        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(ready_only=True))

        assert len(result.episodes) == 0
        assert result.total_matching == 0


class TestDigestEpisodeSelectorExcludeDigested:
    """Tests for exclude_digested mode (THES-154)."""

    def test_exclude_digested_filters_out_already_digested(
        self, mock_repository, mock_digest_repository, sample_podcast
    ):
        """Test that exclude_digested filters out episodes already in a digest."""
        # Create episodes - ep1 and ep3 are already in a digest
        ep1 = make_episode("ep1", "Already Digested 1", 1, EpisodeState.SUMMARIZED)
        ep2 = make_episode("ep2", "Not Digested", 2, EpisodeState.SUMMARIZED)
        ep3 = make_episode("ep3", "Already Digested 2", 3, EpisodeState.SUMMARIZED)
        episodes = [
            (sample_podcast, ep1),
            (sample_podcast, ep2),
            (sample_podcast, ep3),
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 3)

        # ep1 and ep3 are in a digest (use actual episode.id UUIDs)
        digested_ids = {ep1.id, ep3.id}
        mock_digest_repository.is_episode_in_any_digest.side_effect = lambda eid: eid in digested_ids

        selector = DigestEpisodeSelector(mock_repository, mock_digest_repository)
        result = selector.select(DigestSelectionCriteria(ready_only=True, exclude_digested=True))

        # Only ep2 should be selected
        assert len(result.episodes) == 1
        assert result.episodes[0][1].external_id == "ep2"

    def test_exclude_digested_without_digest_repository_does_nothing(self, mock_repository, sample_podcast):
        """Test that exclude_digested is ignored when no digest repository is provided."""
        episodes = [
            (sample_podcast, make_episode("ep1", "Episode 1", 1, EpisodeState.SUMMARIZED)),
            (sample_podcast, make_episode("ep2", "Episode 2", 2, EpisodeState.SUMMARIZED)),
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 2)

        # No digest repository provided
        selector = DigestEpisodeSelector(mock_repository)
        result = selector.select(DigestSelectionCriteria(ready_only=True, exclude_digested=True))

        # All episodes should be selected (exclude_digested is ignored)
        assert len(result.episodes) == 2

    def test_exclude_digested_works_with_normal_mode(self, mock_repository, mock_digest_repository, sample_podcast):
        """Test that exclude_digested works in normal (non-ready_only) mode."""
        # ep1 needs processing but is already in a digest, ep2 needs processing
        ep1 = make_episode("ep1", "Needs Processing 1", 1, EpisodeState.DISCOVERED)
        ep2 = make_episode("ep2", "Needs Processing 2", 2, EpisodeState.DISCOVERED)
        episodes = [
            (sample_podcast, ep1),
            (sample_podcast, ep2),
        ]
        mock_repository.get_all_episodes.return_value = (episodes, 2)

        # ep1 is already in a digest (use actual episode.id UUID)
        mock_digest_repository.is_episode_in_any_digest.side_effect = lambda eid: eid == ep1.id

        selector = DigestEpisodeSelector(mock_repository, mock_digest_repository)
        result = selector.select(DigestSelectionCriteria(exclude_digested=True))

        # Only ep2 should be selected
        assert len(result.episodes) == 1
        assert result.episodes[0][1].external_id == "ep2"


class TestDigestSelectionCriteriaNewFlags:
    """Tests for new ready_only and exclude_digested flags."""

    def test_default_flags_are_false(self):
        """Test that new flags default to False."""
        criteria = DigestSelectionCriteria()
        assert criteria.ready_only is False
        assert criteria.exclude_digested is False

    def test_flags_can_be_set(self):
        """Test that new flags can be set."""
        criteria = DigestSelectionCriteria(ready_only=True, exclude_digested=True)
        assert criteria.ready_only is True
        assert criteria.exclude_digested is True
