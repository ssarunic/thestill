"""
Unit tests for PodcastService.

Tests the service layer business logic with mock repositories and path managers.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from thestill.models.podcast import Episode, Podcast
from thestill.repositories.podcast_repository import PodcastRepository
from thestill.services.podcast_service import EpisodeWithIndex, PodcastService, PodcastWithIndex
from thestill.utils.path_manager import PathManager


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_repository():
    """Create mock podcast repository."""
    return Mock(spec=PodcastRepository)


@pytest.fixture
def mock_path_manager(temp_storage):
    """Create real PathManager for testing."""
    return PathManager(str(temp_storage))


@pytest.fixture
def sample_podcasts():
    """Create sample podcasts for testing."""
    return [
        Podcast(
            title="Tech Talk",
            description="A tech podcast",
            rss_url="https://example.com/tech.xml",
            episodes=[
                Episode(
                    title="Episode 1",
                    audio_url="https://example.com/ep1.mp3",
                    external_id="ep1",
                    pub_date=datetime(2025, 1, 10),
                    description="First episode",
                    raw_transcript_path="ep1_raw.json",
                    clean_transcript_path="ep1_clean.md",
                ),
                Episode(
                    title="Episode 2",
                    audio_url="https://example.com/ep2.mp3",
                    external_id="ep2",
                    pub_date=datetime(2025, 1, 5),
                    description="Second episode",
                ),
            ],
        ),
        Podcast(
            title="News Daily",
            description="Daily news",
            rss_url="https://example.com/news.xml",
            episodes=[
                Episode(
                    title="News Today",
                    audio_url="https://example.com/news1.mp3",
                    external_id="news1",
                    pub_date=datetime(2025, 1, 15),
                    description="Today's news",
                ),
            ],
        ),
    ]


@pytest.fixture
def mock_feed_manager():
    """Create mock feed manager."""
    mock_fm = MagicMock()
    mock_fm.add_podcast = MagicMock(return_value=True)
    mock_fm.remove_podcast = MagicMock(return_value=True)
    mock_fm.list_podcasts = MagicMock(return_value=[])
    return mock_fm


@pytest.fixture
def podcast_service(temp_storage, mock_repository, mock_path_manager, mock_feed_manager):
    """Create PodcastService with mocked dependencies."""
    service = PodcastService(temp_storage, mock_repository, mock_path_manager)
    # Replace feed_manager with mock
    service.feed_manager = mock_feed_manager
    return service


class TestPodcastServiceInitialization:
    """Test PodcastService initialization."""

    def test_init_with_string_path(self, mock_repository, mock_path_manager):
        """Should accept string path."""
        service = PodcastService("/tmp/test", mock_repository, mock_path_manager)
        assert service.storage_path == Path("/tmp/test")
        assert service.repository is mock_repository
        assert service.path_manager is mock_path_manager

    def test_init_with_path_object(self, mock_repository, mock_path_manager):
        """Should accept Path object."""
        path_obj = Path("/tmp/test")
        service = PodcastService(path_obj, mock_repository, mock_path_manager)
        assert service.storage_path == path_obj
        assert service.repository is mock_repository
        assert service.path_manager is mock_path_manager


class TestAddPodcast:
    """Test add_podcast method."""

    def test_add_podcast_success(self, podcast_service, sample_podcasts):
        """Should add podcast and return it."""
        # Setup mocks
        podcast_service.feed_manager.add_podcast.return_value = True
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.add_podcast("https://example.com/tech.xml")

        # Verify
        assert result is not None
        assert result.title == "News Daily"  # Last podcast in list
        podcast_service.feed_manager.add_podcast.assert_called_once_with("https://example.com/tech.xml")

    def test_add_podcast_failure(self, podcast_service):
        """Should return None on failure."""
        # Setup mocks
        podcast_service.feed_manager.add_podcast.return_value = False

        # Execute
        result = podcast_service.add_podcast("https://example.com/bad.xml")

        # Verify
        assert result is None


class TestRemovePodcast:
    """Test remove_podcast method."""

    def test_remove_podcast_by_index(self, podcast_service, sample_podcasts):
        """Should remove podcast by index."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts
        podcast_service.feed_manager.remove_podcast.return_value = True

        # Execute
        result = podcast_service.remove_podcast(1)

        # Verify
        assert result is True
        podcast_service.feed_manager.remove_podcast.assert_called_once_with("https://example.com/tech.xml")

    def test_remove_podcast_by_url(self, podcast_service, sample_podcasts):
        """Should remove podcast by URL."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts
        podcast_service.feed_manager.remove_podcast.return_value = True

        # Execute
        result = podcast_service.remove_podcast("https://example.com/news.xml")

        # Verify
        assert result is True
        podcast_service.feed_manager.remove_podcast.assert_called_once_with("https://example.com/news.xml")

    def test_remove_podcast_not_found(self, podcast_service, sample_podcasts):
        """Should return False if podcast not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.remove_podcast(999)

        # Verify
        assert result is False
        podcast_service.feed_manager.remove_podcast.assert_not_called()


class TestGetPodcasts:
    """Test get_podcasts method."""

    def test_get_podcasts_empty(self, podcast_service):
        """Should return empty list when no podcasts."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = []

        # Execute
        result = podcast_service.get_podcasts()

        # Verify
        assert result == []

    def test_get_podcasts_with_data(self, podcast_service, sample_podcasts):
        """Should return podcasts with index numbers."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcasts()

        # Verify
        assert len(result) == 2
        assert isinstance(result[0], PodcastWithIndex)
        assert result[0].index == 1
        assert result[0].title == "Tech Talk"
        assert result[0].episodes_count == 2
        assert result[0].episodes_processed == 1

        assert result[1].index == 2
        assert result[1].title == "News Daily"
        assert result[1].episodes_count == 1
        assert result[1].episodes_processed == 0


class TestGetPodcast:
    """Test get_podcast method."""

    def test_get_podcast_by_valid_index(self, podcast_service, sample_podcasts):
        """Should get podcast by 1-based index."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcast(1)

        # Verify
        assert result is not None
        assert result.title == "Tech Talk"

    def test_get_podcast_by_invalid_index(self, podcast_service, sample_podcasts):
        """Should return None for invalid index."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcast(999)

        # Verify
        assert result is None

    def test_get_podcast_by_url(self, podcast_service, sample_podcasts):
        """Should get podcast by RSS URL."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcast("https://example.com/news.xml")

        # Verify
        assert result is not None
        assert result.title == "News Daily"

    def test_get_podcast_by_string_number(self, podcast_service, sample_podcasts):
        """Should convert string number to int."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcast("2")

        # Verify
        assert result is not None
        assert result.title == "News Daily"

    def test_get_podcast_not_found(self, podcast_service, sample_podcasts):
        """Should return None if URL not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_podcast("https://example.com/notfound.xml")

        # Verify
        assert result is None


class TestGetEpisode:
    """Test get_episode method."""

    def test_get_episode_by_index(self, podcast_service, sample_podcasts):
        """Should get episode by 1-based index (1=latest)."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute - Episode 1 has pub_date 2025-01-10, Episode 2 has 2025-01-05
        # Sorted by date desc, so index 1 = Episode 1 (latest)
        result = podcast_service.get_episode(1, 1)

        # Verify
        assert result is not None
        assert result.title == "Episode 1"  # Latest by date

    def test_get_episode_latest_keyword(self, podcast_service, sample_podcasts):
        """Should get latest episode with 'latest' keyword."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episode(1, "latest")

        # Verify
        assert result is not None
        assert result.title == "Episode 1"

    def test_get_episode_by_date(self, podcast_service, sample_podcasts):
        """Should get episode by date string."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episode(1, "2025-01-05")

        # Verify
        assert result is not None
        assert result.title == "Episode 2"

    def test_get_episode_by_guid(self, podcast_service, sample_podcasts):
        """Should get episode by GUID."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episode(1, "ep2")

        # Verify
        assert result is not None
        assert result.title == "Episode 2"

    def test_get_episode_string_number(self, podcast_service, sample_podcasts):
        """Should convert string number to int."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episode(1, "2")

        # Verify
        assert result is not None
        assert result.title == "Episode 2"

    def test_get_episode_podcast_not_found(self, podcast_service, sample_podcasts):
        """Should return None if podcast not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episode(999, 1)

        # Verify
        assert result is None

    def test_get_episode_no_episodes(self, podcast_service):
        """Should return None if podcast has no episodes."""
        # Setup mocks
        empty_podcast = Podcast(
            title="Empty",
            description="No episodes",
            rss_url="https://example.com/empty.xml",
            episodes=[],
        )
        podcast_service.feed_manager.list_podcasts.return_value = [empty_podcast]

        # Execute
        result = podcast_service.get_episode(1, 1)

        # Verify
        assert result is None


class TestGetEpisodes:
    """Test get_episodes method."""

    def test_get_episodes_basic(self, podcast_service, sample_podcasts, mock_path_manager):
        """Should list episodes with indices."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts
        # Mock path_manager methods to return non-existent paths
        mock_path_manager.raw_transcript_file = Mock(return_value=Path("/nonexistent"))
        mock_path_manager.clean_transcript_file = Mock(return_value=Path("/nonexistent"))
        mock_path_manager.summary_file = Mock(return_value=Path("/nonexistent"))

        # Execute
        result = podcast_service.get_episodes(1)

        # Verify
        assert len(result) == 2
        assert isinstance(result[0], EpisodeWithIndex)
        assert result[0].podcast_index == 1
        assert result[0].episode_index == 1  # Latest by date
        assert result[0].title == "Episode 1"
        assert result[0].state == "cleaned"

    def test_get_episodes_podcast_not_found(self, podcast_service, sample_podcasts):
        """Should return None if podcast not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_episodes(999)

        # Verify
        assert result is None


class TestGetTranscript:
    """Test get_transcript method."""

    def test_get_transcript_success(self, podcast_service, sample_podcasts, temp_storage):
        """Should return transcript content."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Create actual transcript file
        transcript_path = temp_storage / "clean_transcripts" / "ep1_clean.md"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text("# Episode 1 Transcript\nThis is the content.")

        # Execute
        result = podcast_service.get_transcript(1, 1)

        # Verify - result is now a TranscriptResult object
        assert result is not None
        assert "Episode 1 Transcript" in result.content
        assert "This is the content" in result.content
        assert result.transcript_type == "cleaned"

    def test_get_transcript_episode_not_found(self, podcast_service, sample_podcasts):
        """Should return None if episode not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute
        result = podcast_service.get_transcript(1, "nonexistent")

        # Verify
        assert result is None

    def test_get_transcript_not_processed(self, podcast_service, sample_podcasts):
        """Should return N/A message if not processed."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute - Episode 2 is not processed
        result = podcast_service.get_transcript(1, 2)

        # Verify - result is now a TranscriptResult object
        assert result is not None
        assert "N/A" in result.content
        assert result.transcript_type is None

    def test_get_transcript_file_not_found(self, podcast_service, sample_podcasts):
        """Should return N/A message if file not found."""
        # Setup mocks
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts

        # Execute - Episode 1 is processed but file doesn't exist
        result = podcast_service.get_transcript(1, 1)

        # Verify - result is now a TranscriptResult object
        assert result is not None
        assert "N/A" in result.content
        assert result.transcript_type is None


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_zero_index(self, podcast_service, sample_podcasts):
        """Should handle zero index gracefully."""
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts
        result = podcast_service.get_podcast(0)
        assert result is None

    def test_negative_index(self, podcast_service, sample_podcasts):
        """Should handle negative index gracefully."""
        podcast_service.feed_manager.list_podcasts.return_value = sample_podcasts
        result = podcast_service.get_podcast(-1)
        assert result is None

    def test_empty_url(self, podcast_service):
        """Should handle empty URL gracefully."""
        podcast_service.feed_manager.list_podcasts.return_value = []
        result = podcast_service.get_podcast("")
        assert result is None
