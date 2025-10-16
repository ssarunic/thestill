"""
Unit tests for PodcastFeedManager.

Tests RSS parsing, YouTube integration, and episode discovery with mocked dependencies.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from time import struct_time
from unittest.mock import MagicMock, Mock, patch

import pytest

from thestill.core.feed_manager import PodcastFeedManager
from thestill.models.podcast import Episode, Podcast
from thestill.repositories.podcast_repository import PodcastRepository
from thestill.utils.path_manager import PathManager


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_repository():
    """Create mock podcast repository."""
    mock_repo = Mock(spec=PodcastRepository)
    mock_repo.exists = Mock(return_value=False)
    mock_repo.save = Mock(return_value=True)
    mock_repo.delete = Mock(return_value=True)
    mock_repo.get_all = Mock(return_value=[])
    mock_repo.get_by_url = Mock(return_value=None)
    mock_repo.update = Mock(return_value=True)
    return mock_repo


@pytest.fixture
def path_manager(temp_storage):
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
                ),
                Episode(
                    title="Episode 2",
                    audio_url="https://example.com/ep2.mp3",
                    external_id="ep2",
                    pub_date=datetime(2025, 1, 5),
                    description="Second episode",
                    audio_path="ep2.mp3",
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
def feed_manager(mock_repository, path_manager):
    """Create FeedManager with mocked dependencies."""
    manager = PodcastFeedManager(mock_repository, path_manager)
    # Mock media source factory to avoid actual network calls
    manager.media_source_factory = Mock()
    manager.media_source_factory.detect_source = Mock()
    return manager


class TestFeedManagerInitialization:
    """Test FeedManager initialization."""

    def test_init_creates_storage_directory(self, mock_repository, temp_storage):
        """Should create storage directory if it doesn't exist."""
        storage_path = temp_storage / "new_dir"
        path_manager = PathManager(str(storage_path))

        manager = PodcastFeedManager(mock_repository, path_manager)

        assert manager.repository is mock_repository
        assert manager.path_manager is path_manager
        assert storage_path.exists()

    def test_init_with_existing_directory(self, mock_repository, path_manager):
        """Should work with existing directory."""
        manager = PodcastFeedManager(mock_repository, path_manager)

        assert manager.storage_path.exists()


class TestAddPodcast:
    """Test add_podcast method."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_rss_podcast_success(self, mock_parse, feed_manager, mock_repository):
        """Should add RSS podcast successfully."""
        # Setup mock media source to return RSS metadata
        mock_source = Mock()
        mock_source.extract_metadata.return_value = {
            "title": "Test Podcast",
            "description": "A test podcast",
            "rss_url": "https://example.com/feed.xml",
        }
        feed_manager.media_source_factory.detect_source.return_value = mock_source
        mock_repository.exists.return_value = False
        mock_repository.save.return_value = True

        # Execute
        result = feed_manager.add_podcast("https://example.com/feed.xml")

        # Verify
        assert result is True
        mock_repository.save.assert_called_once()
        saved_podcast = mock_repository.save.call_args[0][0]
        assert saved_podcast.title == "Test Podcast"
        assert str(saved_podcast.rss_url) == "https://example.com/feed.xml"

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_podcast_already_exists(self, mock_parse, feed_manager, mock_repository):
        """Should return False if podcast already exists."""
        # Setup
        mock_feed = Mock()
        mock_feed.bozo = False
        mock_feed.feed = {"title": "Existing", "description": ""}
        mock_parse.return_value = mock_feed

        mock_repository.exists.return_value = True

        # Execute
        result = feed_manager.add_podcast("https://example.com/feed.xml")

        # Verify
        assert result is False
        mock_repository.save.assert_not_called()

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_podcast_invalid_rss(self, mock_parse, feed_manager):
        """Should return False for invalid RSS feed."""
        # Setup - bozo flag indicates invalid feed
        mock_feed = Mock()
        mock_feed.bozo = True
        mock_parse.return_value = mock_feed

        # Execute
        result = feed_manager.add_podcast("https://example.com/bad.xml")

        # Verify
        assert result is False

    def test_add_youtube_podcast(self, feed_manager, mock_repository):
        """Should handle YouTube URLs."""
        # Setup - mock media source factory to return YouTube metadata
        mock_source = Mock()
        mock_source.extract_metadata.return_value = {
            "title": "YouTube Podcast",
            "description": "A YouTube channel",
            "rss_url": "https://youtube.com/watch?v=123",
        }
        feed_manager.media_source_factory.detect_source.return_value = mock_source
        mock_repository.exists.return_value = False
        mock_repository.save.return_value = True

        # Execute
        result = feed_manager.add_podcast("https://youtube.com/watch?v=123")

        # Verify
        assert result is True
        mock_repository.save.assert_called_once()


class TestRemovePodcast:
    """Test remove_podcast method."""

    def test_remove_podcast_success(self, feed_manager, mock_repository):
        """Should remove podcast successfully."""
        mock_repository.delete.return_value = True

        result = feed_manager.remove_podcast("https://example.com/feed.xml")

        assert result is True
        mock_repository.delete.assert_called_once_with("https://example.com/feed.xml")

    def test_remove_podcast_not_found(self, feed_manager, mock_repository):
        """Should return False if podcast not found."""
        mock_repository.delete.return_value = False

        result = feed_manager.remove_podcast("https://example.com/notfound.xml")

        assert result is False


class TestListPodcasts:
    """Test list_podcasts method."""

    def test_list_podcasts_empty(self, feed_manager, mock_repository):
        """Should return empty list when no podcasts."""
        mock_repository.get_all.return_value = []

        result = feed_manager.list_podcasts()

        assert result == []

    def test_list_podcasts_with_data(self, feed_manager, mock_repository, sample_podcasts):
        """Should return all podcasts."""
        mock_repository.get_all.return_value = sample_podcasts

        result = feed_manager.list_podcasts()

        assert len(result) == 2
        assert result[0].title == "Tech Talk"
        assert result[1].title == "News Daily"


class TestGetNewEpisodes:
    """Test get_new_episodes method - simplified tests."""

    def test_get_new_episodes_youtube(self, feed_manager, mock_repository):
        """Should handle YouTube podcasts."""
        # Setup YouTube podcast
        youtube_podcast = Podcast(
            title="YouTube Channel",
            description="YT",
            rss_url="https://youtube.com/channel/123",
            episodes=[],
        )
        mock_repository.get_all.return_value = [youtube_podcast]

        # Setup media source to return YouTube episodes
        mock_source = Mock()
        mock_source.fetch_episodes.return_value = [
            Episode(
                title="YT Video",
                audio_url="https://youtube.com/watch?v=abc",
                external_id="yt-abc",
                pub_date=datetime(2025, 1, 20),
                description="Video",
            )
        ]
        feed_manager.media_source_factory.detect_source.return_value = mock_source

        # Execute
        result = feed_manager.get_new_episodes()

        # Verify
        assert len(result) == 1
        podcast, episodes = result[0]
        assert len(episodes) == 1
        assert episodes[0].title == "YT Video"


class TestEpisodeMarking:
    """Test episode marking methods - use repository.update_episode API."""

    def test_mark_episode_downloaded(self, feed_manager, mock_repository):
        """Should call repository.update_episode for downloaded episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_downloaded("https://example.com/feed.xml", "ep1", "/path/to/audio.mp3")

        # Verify
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml", "ep1", {"audio_path": "/path/to/audio.mp3"}
        )

    def test_mark_episode_downsampled(self, feed_manager, mock_repository):
        """Should call repository.update_episode for downsampled episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_downsampled("https://example.com/feed.xml", "ep1", "/path/to/audio_16k.wav")

        # Verify
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml",
            "ep1",
            {"downsampled_audio_path": "/path/to/audio_16k.wav"},
        )

    def test_mark_episode_processed(self, feed_manager, mock_repository):
        """Should call repository.update_episode for processed episodes."""
        mock_repository.update_episode = Mock(return_value=True)

        # Execute
        feed_manager.mark_episode_processed(
            "https://example.com/feed.xml",
            "ep1",
            raw_transcript_path="/path/to/transcript.json",
            summary_path="/path/to/summary.md",
        )

        # Verify
        mock_repository.update_episode.assert_called_once()
        call_args = mock_repository.update_episode.call_args[0]
        assert call_args[0] == "https://example.com/feed.xml"
        assert call_args[1] == "ep1"
        updates = call_args[2]
        # No longer sets processed=True - state is auto-computed from file paths
        assert updates["raw_transcript_path"] == "/path/to/transcript.json"
        assert updates["summary_path"] == "/path/to/summary.md"


class TestEdgeCases:
    """Test edge cases and error handling."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_podcast_network_error(self, mock_parse, feed_manager):
        """Should handle network errors gracefully."""
        mock_parse.side_effect = Exception("Network error")

        result = feed_manager.add_podcast("https://example.com/feed.xml")

        assert result is False

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_get_new_episodes_malformed_feed(self, mock_parse, feed_manager, mock_repository):
        """Should skip podcasts with malformed feeds."""
        podcast = Podcast(title="Bad Feed", description="", rss_url="https://example.com/bad.xml", episodes=[])
        mock_repository.get_all.return_value = [podcast]

        # Simulate parsing error
        mock_parse.side_effect = Exception("Parse error")

        # Execute
        result = feed_manager.get_new_episodes()

        # Verify - should return empty list, not crash
        assert result == []

    def test_mark_episode_not_found(self, feed_manager, mock_repository):
        """Should handle marking non-existent episode gracefully."""
        mock_repository.update_episode = Mock(return_value=False)

        # Should not raise exception
        feed_manager.mark_episode_downloaded("https://notfound.xml", "ep1", "/path")

        # Verify - called but returned False
        mock_repository.update_episode.assert_called_once()


class TestTransactionContextManager:
    """Test transaction context manager for batch updates."""

    def test_transaction_batches_saves(self, feed_manager, mock_repository, sample_podcasts):
        """Should defer repository saves until transaction completes."""
        # Setup: Mock repository to return a podcast
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates within transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
            feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")
            feed_manager.mark_episode_processed(str(podcast.rss_url), "ep1", raw_transcript_path="transcript.json")

            # Verify: Repository save NOT called yet (still in transaction)
            mock_repository.save.assert_not_called()
            # Verify: Repository update_episode NOT called (transaction mode)
            mock_repository.update_episode.assert_not_called()

        # Verify: Repository save called ONCE after transaction completes
        from ..models.podcast import EpisodeState

        assert mock_repository.save.call_count == 1
        saved_podcast = mock_repository.save.call_args[0][0]
        assert saved_podcast.episodes[0].audio_path == "audio.mp3"
        assert saved_podcast.episodes[0].downsampled_audio_path == "audio_16k.wav"
        assert saved_podcast.episodes[0].raw_transcript_path == "transcript.json"
        assert saved_podcast.episodes[0].state == EpisodeState.TRANSCRIBED

    def test_transaction_single_save_per_podcast(self, feed_manager, mock_repository, sample_podcasts):
        """Should save each podcast only once even with multiple episode updates."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates to different episodes
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio1.mp3")
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep2", "audio2.mp3")

        # Verify: Repository save called exactly once for the podcast
        assert mock_repository.save.call_count == 1

    def test_transaction_multiple_podcasts(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle updates to multiple podcasts in one transaction."""
        # Setup
        podcast1 = sample_podcasts[0]
        podcast2 = sample_podcasts[1]

        def find_by_url_side_effect(url):
            if url == str(podcast1.rss_url):
                return podcast1
            elif url == str(podcast2.rss_url):
                return podcast2
            return None

        mock_repository.get_by_url.side_effect = find_by_url_side_effect

        # Execute
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast1.rss_url), "ep1", "audio1.mp3")
            feed_manager.mark_episode_downloaded(str(podcast2.rss_url), "news1", "news1.mp3")

        # Verify: Repository save called twice (once per podcast)
        assert mock_repository.save.call_count == 2

    def test_transaction_nested_is_noop(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle nested transactions (inner is no-op)."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Nested transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")

            # Inner transaction (should be no-op)
            with feed_manager.transaction():
                feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")

            # Still in outer transaction
            mock_repository.save.assert_not_called()

        # Verify: Save called once after outer transaction
        assert mock_repository.save.call_count == 1

    def test_transaction_persists_on_exception(self, feed_manager, mock_repository, sample_podcasts):
        """Should still persist changes if exception occurs in transaction."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Exception within transaction
        try:
            with feed_manager.transaction():
                feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Verify: Save still called (no rollback in current implementation)
        assert mock_repository.save.call_count == 1

    def test_transaction_episode_not_found(self, feed_manager, mock_repository, sample_podcasts):
        """Should handle episode not found within transaction."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Update non-existent episode
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "nonexistent", "audio.mp3")

        # Verify: Save called but episode not found (logged warning)
        assert mock_repository.save.call_count == 1

    def test_transaction_podcast_not_found(self, feed_manager, mock_repository):
        """Should handle podcast not found within transaction."""
        # Setup
        mock_repository.get_by_url.return_value = None

        # Execute: Update episode on non-existent podcast
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded("https://notfound.xml", "ep1", "audio.mp3")

        # Verify: No save called (no podcast to save)
        mock_repository.save.assert_not_called()

    def test_transaction_caches_podcast(self, feed_manager, mock_repository, sample_podcasts):
        """Should cache podcast on first access and reuse for subsequent updates."""
        # Setup
        podcast = sample_podcasts[0]
        mock_repository.get_by_url.return_value = podcast

        # Execute: Multiple updates within transaction
        with feed_manager.transaction():
            feed_manager.mark_episode_downloaded(str(podcast.rss_url), "ep1", "audio.mp3")
            feed_manager.mark_episode_downsampled(str(podcast.rss_url), "ep1", "audio_16k.wav")

        # Verify: Repository find_by_url called only once (cached)
        assert mock_repository.get_by_url.call_count == 1

    def test_without_transaction_uses_repository_directly(self, feed_manager, mock_repository):
        """Should use repository.update_episode when not in transaction."""
        # Setup
        mock_repository.update_episode = Mock(return_value=True)

        # Execute: Update without transaction
        feed_manager.mark_episode_downloaded("https://example.com/feed.xml", "ep1", "audio.mp3")

        # Verify: Repository update_episode called directly
        mock_repository.update_episode.assert_called_once_with(
            "https://example.com/feed.xml", "ep1", {"audio_path": "audio.mp3"}
        )
        # Verify: Repository save NOT called
        mock_repository.save.assert_not_called()
