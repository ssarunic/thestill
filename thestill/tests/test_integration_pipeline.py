"""
Integration tests for the full podcast processing pipeline.

Tests the end-to-end workflow: add → refresh → download → downsample → transcribe → clean
Uses mocked external dependencies (network, LLM) to avoid costs and ensure repeatability.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from thestill.core.audio_downloader import AudioDownloader
from thestill.core.audio_preprocessor import AudioPreprocessor
from thestill.core.feed_manager import PodcastFeedManager
from thestill.models.podcast import Episode, EpisodeState, Podcast
from thestill.repositories.json_podcast_repository import JsonPodcastRepository
from thestill.services.podcast_service import PodcastService
from thestill.services.refresh_service import RefreshService
from thestill.utils.path_manager import PathManager


@pytest.fixture
def temp_storage():
    """Create temporary storage directory with full structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = Path(tmpdir)
        # Create all required subdirectories
        (storage / "original_audio").mkdir(parents=True)
        (storage / "downsampled_audio").mkdir(parents=True)
        (storage / "raw_transcripts").mkdir(parents=True)
        (storage / "clean_transcripts").mkdir(parents=True)
        (storage / "summaries").mkdir(parents=True)
        yield storage


@pytest.fixture
def path_manager(temp_storage):
    """Create PathManager for temporary storage."""
    return PathManager(str(temp_storage))


@pytest.fixture
def repository(temp_storage):
    """Create real JsonPodcastRepository for integration testing."""
    return JsonPodcastRepository(str(temp_storage))


@pytest.fixture
def feed_manager(repository, path_manager):
    """Create FeedManager with real repository and path manager."""
    return PodcastFeedManager(podcast_repository=repository, path_manager=path_manager)


@pytest.fixture
def podcast_service(temp_storage, repository, path_manager):
    """Create PodcastService for integration testing."""
    return PodcastService(storage_path=temp_storage, podcast_repository=repository, path_manager=path_manager)


@pytest.fixture
def refresh_service(feed_manager, podcast_service):
    """Create RefreshService for integration testing."""
    return RefreshService(feed_manager=feed_manager, podcast_service=podcast_service)


@pytest.fixture
def sample_rss_feed():
    """Sample RSS feed data for mocking feedparser."""
    # Create a mock that mimics feedparser.FeedParserDict behavior
    feed_data = Mock()
    feed_data.feed = {"title": "Test Podcast", "description": "A test podcast"}
    feed_data.entries = [
        {
            "title": "Episode 1",
            "description": "First episode",
            "guid": "ep1",  # feedparser returns "guid" from RSS feed
            "published_parsed": datetime(2025, 1, 1).timetuple(),
            "itunes_duration": "30:00",
            "links": [{"type": "audio/mpeg", "href": "https://example.com/ep1.mp3"}],
            "enclosures": [],
        },
        {
            "title": "Episode 2",
            "description": "Second episode",
            "guid": "ep2",  # feedparser returns "guid" from RSS feed
            "published_parsed": datetime(2025, 1, 2).timetuple(),
            "itunes_duration": "25:00",
            "links": [{"type": "audio/mpeg", "href": "https://example.com/ep2.mp3"}],
            "enclosures": [],
        },
    ]
    feed_data.bozo = False
    return feed_data


class TestPipelineAddAndRefresh:
    """Test the add podcast and refresh workflow."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_add_podcast_and_discover_episodes(self, mock_parse, podcast_service, refresh_service, sample_rss_feed):
        """Test adding a podcast and discovering episodes."""
        # Mock feedparser to return sample feed
        mock_parse.return_value = sample_rss_feed

        # Step 1: Add podcast
        podcast = podcast_service.add_podcast("https://example.com/feed.xml")
        assert podcast is not None
        assert podcast.title == "Test Podcast"

        # Verify podcast was saved
        podcasts = podcast_service.list_podcasts()
        assert len(podcasts) == 1
        assert podcasts[0].title == "Test Podcast"

        # Step 2: Refresh to discover episodes
        result = refresh_service.refresh()
        assert result.total_episodes == 2
        assert len(result.episodes_by_podcast) == 1

        # Verify episodes were discovered
        podcast_with_episodes, episodes = result.episodes_by_podcast[0]
        assert len(episodes) == 2
        assert episodes[0].title == "Episode 1"
        assert episodes[1].title == "Episode 2"
        assert episodes[0].state == EpisodeState.DISCOVERED

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_refresh_idempotency(self, mock_parse, refresh_service, podcast_service, feed_manager, sample_rss_feed):
        """Test that refreshing twice doesn't duplicate episodes."""
        mock_parse.return_value = sample_rss_feed

        # Add podcast
        podcast_service.add_podcast("https://example.com/feed.xml")

        # First refresh
        result1 = refresh_service.refresh()
        assert result1.total_episodes == 2

        # Mark episodes as processed to prevent re-discovery
        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        for episode in podcast.episodes:
            feed_manager.mark_episode_processed(str(podcast.rss_url), episode.external_id)

        # Second refresh (should find no new episodes since all are processed)
        result2 = refresh_service.refresh()
        assert result2.total_episodes == 0

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_refresh_with_max_episodes_limit(self, mock_parse, refresh_service, podcast_service, sample_rss_feed):
        """Test that max_episodes limit is respected."""
        mock_parse.return_value = sample_rss_feed

        # Add podcast
        podcast_service.add_podcast("https://example.com/feed.xml")

        # Refresh with limit of 1 episode per podcast
        result = refresh_service.refresh(max_episodes=1)
        assert result.total_episodes == 1  # Only 1 episode due to limit

        # Verify only 1 episode per podcast in result
        podcast_with_episodes, episodes = result.episodes_by_podcast[0]
        assert len(episodes) == 1  # Limited to 1


class TestPipelineDownload:
    """Test the download workflow."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_episode_workflow(
        self, mock_get, mock_parse, podcast_service, feed_manager, path_manager, sample_rss_feed, temp_storage
    ):
        """Test downloading episode audio files."""
        # Mock feedparser
        mock_parse.return_value = sample_rss_feed

        # Mock HTTP download
        mock_response = Mock()
        mock_response.headers = {"content-length": "1024"}
        mock_response.iter_content = Mock(return_value=[b"fake audio data"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Add and refresh
        podcast_service.add_podcast("https://example.com/feed.xml")
        feed_manager.get_new_episodes()

        # Get episodes to download
        episodes_to_download = feed_manager.get_episodes_to_download(str(temp_storage))
        assert len(episodes_to_download) == 1
        podcast, episodes = episodes_to_download[0]
        assert len(episodes) == 2

        # Download first episode
        downloader = AudioDownloader(str(path_manager.original_audio_dir()))
        audio_path = downloader.download_episode(episodes[0], podcast.title)

        assert audio_path is not None
        assert Path(audio_path).exists()

        # Mark as downloaded
        feed_manager.mark_episode_downloaded(str(podcast.rss_url), episodes[0].external_id, audio_path)

        # Verify state changed
        updated_podcast = podcast_service.get_podcast(1)
        assert updated_podcast is not None
        updated_episode = next(ep for ep in updated_podcast.episodes if ep.external_id == episodes[0].external_id)
        assert updated_episode.state == EpisodeState.DOWNLOADED
        assert updated_episode.audio_path is not None


class TestPipelineDownsample:
    """Test the downsample workflow."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    @patch("thestill.core.audio_downloader.requests.get")
    def test_downsample_workflow(
        self,
        mock_get,
        mock_parse,
        podcast_service,
        feed_manager,
        path_manager,
        sample_rss_feed,
        temp_storage,
    ):
        """Test downsampling audio files."""
        # Setup: Add podcast and mock download
        mock_parse.return_value = sample_rss_feed
        mock_response = Mock()
        mock_response.headers = {"content-length": "1024"}
        mock_response.iter_content = Mock(return_value=[b"fake audio data"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Add, refresh, and download
        podcast_service.add_podcast("https://example.com/feed.xml")
        feed_manager.get_new_episodes()
        episodes_to_download = feed_manager.get_episodes_to_download(str(temp_storage))
        podcast, episodes = episodes_to_download[0]

        downloader = AudioDownloader(str(path_manager.original_audio_dir()))
        audio_path = downloader.download_episode(episodes[0], podcast.title)
        feed_manager.mark_episode_downloaded(str(podcast.rss_url), episodes[0].external_id, audio_path)

        # Get episodes to downsample
        episodes_to_downsample = feed_manager.get_episodes_to_downsample(str(temp_storage))
        assert len(episodes_to_downsample) == 1
        podcast, episodes = episodes_to_downsample[0]
        assert len(episodes) == 1

        # Simulate downsampling (create fake downsampled file)
        downsampled_filename = f"Test_Podcast_Episode_1_downsampled.wav"
        downsampled_path = path_manager.downsampled_audio_dir() / downsampled_filename
        downsampled_path.write_text("fake downsampled audio")

        # Mark as downsampled
        feed_manager.mark_episode_downsampled(str(podcast.rss_url), episodes[0].external_id, str(downsampled_path))

        # Verify state changed
        updated_podcast = podcast_service.get_podcast(1)
        assert updated_podcast is not None
        updated_episode = next(ep for ep in updated_podcast.episodes if ep.external_id == episodes[0].external_id)
        assert updated_episode.state == EpisodeState.DOWNSAMPLED


class TestPipelineErrorRecovery:
    """Test error recovery and resume scenarios."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_resume_after_failed_download(
        self, mock_parse, podcast_service, feed_manager, sample_rss_feed, temp_storage
    ):
        """Test that pipeline can resume after a failed download."""
        mock_parse.return_value = sample_rss_feed

        # Add and refresh
        podcast_service.add_podcast("https://example.com/feed.xml")
        feed_manager.get_new_episodes()

        # Simulate partial download failure: first episode downloaded, second failed
        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episodes = sorted(podcast.episodes, key=lambda e: e.pub_date or datetime.min, reverse=True)

        # Mark first episode as downloaded
        fake_path = str(Path(temp_storage) / "original_audio" / "fake_ep1.mp3")
        Path(fake_path).parent.mkdir(parents=True, exist_ok=True)
        Path(fake_path).write_text("fake audio")
        feed_manager.mark_episode_downloaded(str(podcast.rss_url), episodes[0].external_id, fake_path)

        # Get episodes to download (should only return the second episode)
        episodes_to_download = feed_manager.get_episodes_to_download(str(temp_storage))
        assert len(episodes_to_download) == 1
        _, remaining_episodes = episodes_to_download[0]
        assert len(remaining_episodes) == 1
        assert remaining_episodes[0].external_id == episodes[1].external_id

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_missing_file_detected(self, mock_parse, podcast_service, feed_manager, sample_rss_feed, temp_storage):
        """Test that missing files are detected and episode is re-queued."""
        mock_parse.return_value = sample_rss_feed

        # Add and refresh
        podcast_service.add_podcast("https://example.com/feed.xml")
        feed_manager.get_new_episodes()

        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = podcast.episodes[0]

        # Mark as downloaded but don't create file
        fake_path = str(Path(temp_storage) / "original_audio" / "nonexistent.mp3")
        feed_manager.mark_episode_downloaded(str(podcast.rss_url), episode.external_id, fake_path)

        # Get episodes to download (should include this episode since file is missing)
        episodes_to_download = feed_manager.get_episodes_to_download(str(temp_storage))
        assert len(episodes_to_download) == 1
        _, episodes = episodes_to_download[0]
        assert len(episodes) == 2  # Both episodes need download (one file missing)


class TestFullPipelineIntegration:
    """Test complete end-to-end pipeline integration."""

    @patch("thestill.core.feed_manager.feedparser.parse")
    @patch("thestill.core.audio_downloader.requests.get")
    def test_full_pipeline_states(
        self,
        mock_get,
        mock_parse,
        podcast_service,
        feed_manager,
        path_manager,
        sample_rss_feed,
        temp_storage,
    ):
        """Test that episode progresses through all states correctly."""
        # Setup mocks
        mock_parse.return_value = sample_rss_feed
        mock_response = Mock()
        mock_response.headers = {"content-length": "1024"}
        mock_response.iter_content = Mock(return_value=[b"fake audio data"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Step 1: Add podcast
        podcast = podcast_service.add_podcast("https://example.com/feed.xml")
        assert podcast is not None

        # Step 2: Refresh - discover episodes
        feed_manager.get_new_episodes()
        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = podcast.episodes[0]
        assert episode.state == EpisodeState.DISCOVERED

        # Step 3: Download
        downloader = AudioDownloader(str(path_manager.original_audio_dir()))
        audio_path = downloader.download_episode(episode, podcast.title)
        feed_manager.mark_episode_downloaded(str(podcast.rss_url), episode.external_id, audio_path)

        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = next(ep for ep in podcast.episodes if ep.external_id == episode.external_id)
        assert episode.state == EpisodeState.DOWNLOADED

        # Step 4: Downsample (simulate)
        downsampled_filename = f"Test_Podcast_Episode_1_downsampled.wav"
        downsampled_path = path_manager.downsampled_audio_dir() / downsampled_filename
        downsampled_path.write_text("fake downsampled audio")
        feed_manager.mark_episode_downsampled(str(podcast.rss_url), episode.external_id, str(downsampled_path))

        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = next(ep for ep in podcast.episodes if ep.external_id == episode.external_id)
        assert episode.state == EpisodeState.DOWNSAMPLED

        # Step 5: Transcribe (mocked - would use real transcriber in actual pipeline)
        transcript_path = "episode_transcript.json"
        transcript_file = path_manager.raw_transcript_file(transcript_path)
        transcript_file.parent.mkdir(parents=True, exist_ok=True)
        transcript_file.write_text(json.dumps({"text": "Sample transcript"}))
        feed_manager.mark_episode_processed(
            str(podcast.rss_url), episode.external_id, raw_transcript_path=transcript_path
        )

        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = next(ep for ep in podcast.episodes if ep.external_id == episode.external_id)
        assert episode.state == EpisodeState.TRANSCRIBED

        # Step 6: Clean (mocked - would use LLM in actual pipeline)
        clean_path = "episode_cleaned.md"
        clean_file = path_manager.clean_transcript_file(clean_path)
        clean_file.parent.mkdir(parents=True, exist_ok=True)
        clean_file.write_text("# Cleaned Transcript\n\nSample cleaned content")
        feed_manager.mark_episode_processed(str(podcast.rss_url), episode.external_id, clean_transcript_path=clean_path)

        podcast = podcast_service.get_podcast(1)
        assert podcast is not None
        episode = next(ep for ep in podcast.episodes if ep.external_id == episode.external_id)
        assert episode.state == EpisodeState.CLEANED
        assert episode.processed is True

    @patch("thestill.core.feed_manager.feedparser.parse")
    def test_multiple_podcasts_isolation(self, mock_parse, podcast_service, feed_manager, sample_rss_feed):
        """Test that multiple podcasts are processed independently."""
        # Create two different RSS feeds
        feed1 = Mock()
        feed1.feed = {"title": "Podcast 1", "description": "First podcast"}
        feed1.entries = [sample_rss_feed.entries[0]]
        feed1.bozo = False

        feed2 = Mock()
        feed2.feed = {"title": "Podcast 2", "description": "Second podcast"}
        feed2.entries = [sample_rss_feed.entries[1]]
        feed2.bozo = False

        # Mock different responses for different URLs
        def mock_parse_side_effect(url):
            if "podcast1" in url:
                return feed1
            return feed2

        mock_parse.side_effect = mock_parse_side_effect

        # Add two podcasts
        podcast1 = podcast_service.add_podcast("https://example.com/podcast1.xml")
        podcast2 = podcast_service.add_podcast("https://example.com/podcast2.xml")

        assert podcast1 is not None
        assert podcast2 is not None
        assert podcast1.title == "Podcast 1"
        assert podcast2.title == "Podcast 2"

        # Verify both podcasts exist
        all_podcasts = podcast_service.list_podcasts()
        assert len(all_podcasts) == 2

        # Refresh to discover episodes
        new_episodes = feed_manager.get_new_episodes()
        assert len(new_episodes) == 2  # Episodes from both podcasts

        # Verify each podcast has its own episodes
        podcast1_refreshed = podcast_service.get_podcast(1)
        podcast2_refreshed = podcast_service.get_podcast(2)
        assert podcast1_refreshed is not None
        assert podcast2_refreshed is not None
        assert len(podcast1_refreshed.episodes) == 1
        assert len(podcast2_refreshed.episodes) == 1
        assert podcast1_refreshed.episodes[0].external_id != podcast2_refreshed.episodes[0].external_id
