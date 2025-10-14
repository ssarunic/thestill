"""
Unit tests for Media Source Strategy Pattern.

Tests RSS and YouTube media source implementations, factory detection logic,
and URL validation.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from time import struct_time
from unittest.mock import MagicMock, Mock, patch

import pytest

from thestill.core.media_source import MediaSourceFactory, RSSMediaSource, YouTubeMediaSource
from thestill.models.podcast import Episode


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def media_factory(temp_storage):
    """Create MediaSourceFactory for testing."""
    return MediaSourceFactory(str(temp_storage))


class TestRSSMediaSource:
    """Test suite for RSSMediaSource."""

    def test_is_valid_url_rss(self):
        """Test RSS URL validation."""
        source = RSSMediaSource()
        assert source.is_valid_url("https://example.com/feed.xml")
        assert source.is_valid_url("https://example.com/feed.rss")
        assert source.is_valid_url("https://example.com/feed/")
        assert source.is_valid_url("https://example.com/rss/")
        assert source.is_valid_url("https://example.com/podcast/")

    def test_is_valid_url_apple_podcasts(self):
        """Test Apple Podcasts URL validation."""
        source = RSSMediaSource()
        assert source.is_valid_url("https://podcasts.apple.com/us/podcast/id123456")
        assert source.is_valid_url("https://itunes.apple.com/us/podcast/id123456")

    def test_is_valid_url_youtube_rejected(self):
        """Test YouTube URLs are rejected by RSS source."""
        source = RSSMediaSource()
        assert not source.is_valid_url("https://www.youtube.com/watch?v=abc123")
        assert not source.is_valid_url("https://www.youtube.com/playlist?list=xyz")
        assert not source.is_valid_url("https://www.youtube.com/@channel")

    def test_extract_metadata_rss(self):
        """Test RSS metadata extraction."""
        source = RSSMediaSource()

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.feed = {"title": "Test Podcast", "description": "A test podcast"}

        with patch("feedparser.parse", return_value=mock_feed):
            metadata = source.extract_metadata("https://example.com/feed.xml")

        assert metadata is not None
        assert metadata["title"] == "Test Podcast"
        assert metadata["description"] == "A test podcast"
        assert metadata["rss_url"] == "https://example.com/feed.xml"

    def test_extract_metadata_invalid_feed(self):
        """Test RSS metadata extraction with invalid feed."""
        source = RSSMediaSource()

        mock_feed = MagicMock()
        mock_feed.bozo = True  # Indicates malformed feed

        with patch("feedparser.parse", return_value=mock_feed):
            metadata = source.extract_metadata("https://example.com/feed.xml")

        assert metadata is None

    @patch("urllib.request.urlopen")
    def test_extract_metadata_apple_podcasts(self, mock_urlopen):
        """Test Apple Podcasts URL resolution to RSS."""
        source = RSSMediaSource()

        # Mock iTunes API response
        mock_response = Mock()
        mock_response.__enter__ = Mock(return_value=mock_response)
        mock_response.__exit__ = Mock(return_value=False)
        mock_response.read.return_value = b'{"resultCount":1,"results":[{"feedUrl":"https://example.com/rss"}]}'
        mock_urlopen.return_value = mock_response

        # Mock feedparser for RSS extraction
        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.feed = {"title": "Apple Podcast", "description": "From iTunes"}

        with patch("feedparser.parse", return_value=mock_feed):
            metadata = source.extract_metadata("https://podcasts.apple.com/us/podcast/id123456")

        assert metadata is not None
        assert metadata["title"] == "Apple Podcast"
        assert metadata["rss_url"] == "https://example.com/rss"

    def test_fetch_episodes(self):
        """Test RSS episode fetching."""
        source = RSSMediaSource()

        # Mock feedparser response
        mock_entry = {
            "title": "Episode 1",
            "description": "First episode",
            "guid": "ep1",
            "published_parsed": struct_time((2025, 1, 10, 12, 0, 0, 0, 0, 0)),
            "itunes_duration": "60:00",
            "links": [{"type": "audio/mpeg", "href": "https://example.com/ep1.mp3"}],
        }

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.entries = [mock_entry]

        with patch("feedparser.parse", return_value=mock_feed):
            episodes = source.fetch_episodes(
                url="https://example.com/feed.xml",
                existing_episodes=[],
                last_processed=None,
                max_episodes=None,
            )

        assert len(episodes) == 1
        assert episodes[0].title == "Episode 1"
        assert episodes[0].guid == "ep1"
        assert str(episodes[0].audio_url) == "https://example.com/ep1.mp3"

    def test_fetch_episodes_filters_processed(self):
        """Test that already processed episodes are filtered out."""
        source = RSSMediaSource()

        # Create existing processed episode
        existing_episode = Episode(
            title="Episode 1",
            audio_url="https://example.com/ep1.mp3",
            guid="ep1",
            pub_date=datetime(2025, 1, 10),
            description="First episode",
            processed=True,
        )

        # Mock feedparser with same episode
        mock_entry = {
            "title": "Episode 1",
            "description": "First episode",
            "guid": "ep1",
            "published_parsed": struct_time((2025, 1, 10, 12, 0, 0, 0, 0, 0)),
            "links": [{"type": "audio/mpeg", "href": "https://example.com/ep1.mp3"}],
        }

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.entries = [mock_entry]

        with patch("feedparser.parse", return_value=mock_feed):
            episodes = source.fetch_episodes(
                url="https://example.com/feed.xml",
                existing_episodes=[existing_episode],
                last_processed=datetime(2025, 1, 5),
                max_episodes=None,
            )

        # Should be empty since episode is already processed
        assert len(episodes) == 0

    def test_fetch_episodes_respects_max_limit(self):
        """Test that max_episodes limit is respected."""
        source = RSSMediaSource()

        # Mock 5 episodes
        mock_entries = []
        for i in range(5):
            mock_entries.append(
                {
                    "title": f"Episode {i+1}",
                    "description": f"Episode {i+1} description",
                    "guid": f"ep{i+1}",
                    "published_parsed": struct_time((2025, 1, i + 1, 12, 0, 0, 0, 0, 0)),
                    "links": [{"type": "audio/mpeg", "href": f"https://example.com/ep{i+1}.mp3"}],
                }
            )

        mock_feed = MagicMock()
        mock_feed.bozo = False
        mock_feed.entries = mock_entries

        with patch("feedparser.parse", return_value=mock_feed):
            episodes = source.fetch_episodes(
                url="https://example.com/feed.xml",
                existing_episodes=[],
                last_processed=None,
                max_episodes=3,
            )

        # Should only return 3 most recent episodes
        assert len(episodes) == 3

    def test_fetch_episodes_invalid_feed(self):
        """Test episode fetching with invalid feed."""
        source = RSSMediaSource()

        mock_feed = MagicMock()
        mock_feed.bozo = True  # Malformed feed

        with patch("feedparser.parse", return_value=mock_feed):
            episodes = source.fetch_episodes(
                url="https://example.com/feed.xml",
                existing_episodes=[],
                last_processed=None,
                max_episodes=None,
            )

        # Should return empty list for invalid feed
        assert len(episodes) == 0

    def test_download_episode_returns_none(self):
        """Test that RSS source returns None for download (delegates to HTTP downloader)."""
        source = RSSMediaSource()

        episode = Episode(
            title="Episode 1",
            audio_url="https://example.com/ep1.mp3",
            guid="ep1",
            pub_date=datetime(2025, 1, 10),
            description="First episode",
        )

        # RSS source should return None (signals standard HTTP download)
        result = source.download_episode(episode, "Test Podcast", "/tmp/storage")
        assert result is None

    def test_parse_date_valid(self):
        """Test date parsing with valid struct_time."""
        source = RSSMediaSource()

        date_tuple = struct_time((2025, 1, 10, 12, 30, 45, 0, 0, 0))
        parsed = source._parse_date(date_tuple)

        assert parsed.year == 2025
        assert parsed.month == 1
        assert parsed.day == 10
        assert parsed.hour == 12
        assert parsed.minute == 30
        assert parsed.second == 45

    def test_parse_date_none(self):
        """Test date parsing with None input."""
        source = RSSMediaSource()

        parsed = source._parse_date(None)

        # Should return current datetime
        assert isinstance(parsed, datetime)
        assert (datetime.now() - parsed).total_seconds() < 1

    def test_extract_audio_url_from_links(self):
        """Test audio URL extraction from entry links."""
        source = RSSMediaSource()

        entry = {
            "links": [
                {"type": "text/html", "href": "https://example.com/page"},
                {"type": "audio/mpeg", "href": "https://example.com/audio.mp3"},
            ]
        }

        audio_url = source._extract_audio_url(entry)
        assert audio_url == "https://example.com/audio.mp3"

    def test_extract_audio_url_from_enclosures(self):
        """Test audio URL extraction from entry enclosures."""
        source = RSSMediaSource()

        entry = {
            "links": [],
            "enclosures": [
                {"type": "audio/mpeg", "href": "https://example.com/audio.mp3"},
            ],
        }

        audio_url = source._extract_audio_url(entry)
        assert audio_url == "https://example.com/audio.mp3"

    def test_extract_audio_url_not_found(self):
        """Test audio URL extraction when no audio found."""
        source = RSSMediaSource()

        entry = {
            "links": [{"type": "text/html", "href": "https://example.com/page"}],
            "enclosures": [],
        }

        audio_url = source._extract_audio_url(entry)
        assert audio_url is None


class TestYouTubeMediaSource:
    """Test suite for YouTubeMediaSource."""

    def test_is_valid_url_youtube(self, temp_storage):
        """Test YouTube URL validation."""
        source = YouTubeMediaSource(str(temp_storage))

        assert source.is_valid_url("https://www.youtube.com/watch?v=abc123")
        assert source.is_valid_url("https://www.youtube.com/playlist?list=xyz")
        assert source.is_valid_url("https://www.youtube.com/@channelname")
        assert source.is_valid_url("https://www.youtube.com/channel/UCabc123")
        assert source.is_valid_url("https://youtu.be/abc123")

    def test_is_valid_url_non_youtube(self, temp_storage):
        """Test non-YouTube URLs are rejected."""
        source = YouTubeMediaSource(str(temp_storage))

        assert not source.is_valid_url("https://example.com/feed.xml")
        assert not source.is_valid_url("https://podcasts.apple.com/us/podcast/id123456")

    def test_extract_metadata(self, temp_storage):
        """Test YouTube metadata extraction."""
        source = YouTubeMediaSource(str(temp_storage))

        mock_info = {
            "title": "Test YouTube Podcast",
            "description": "A test YouTube channel",
            "uploader": "Test Channel",
        }

        source.youtube_downloader.extract_playlist_info = Mock(return_value=mock_info)

        metadata = source.extract_metadata("https://www.youtube.com/@testchannel")

        assert metadata is not None
        assert metadata["title"] == "Test YouTube Podcast"
        assert metadata["description"] == "A test YouTube channel"
        assert metadata["uploader"] == "Test Channel"
        assert metadata["rss_url"] == "https://www.youtube.com/@testchannel"

    def test_extract_metadata_failure(self, temp_storage):
        """Test YouTube metadata extraction failure."""
        source = YouTubeMediaSource(str(temp_storage))

        source.youtube_downloader.extract_playlist_info = Mock(return_value=None)

        metadata = source.extract_metadata("https://www.youtube.com/@testchannel")

        assert metadata is None

    def test_fetch_episodes(self, temp_storage):
        """Test YouTube episode fetching."""
        source = YouTubeMediaSource(str(temp_storage))

        mock_episodes = [
            Episode(
                title="Video 1",
                audio_url="https://www.youtube.com/watch?v=abc123",
                guid="abc123",
                pub_date=datetime(2025, 1, 10),
                description="First video",
            ),
            Episode(
                title="Video 2",
                audio_url="https://www.youtube.com/watch?v=def456",
                guid="def456",
                pub_date=datetime(2025, 1, 9),
                description="Second video",
            ),
        ]

        source.youtube_downloader.get_episodes_from_playlist = Mock(return_value=mock_episodes)

        episodes = source.fetch_episodes(
            url="https://www.youtube.com/@testchannel",
            existing_episodes=[],
            last_processed=None,
            max_episodes=None,
        )

        assert len(episodes) == 2
        assert episodes[0].title == "Video 1"
        assert episodes[1].title == "Video 2"

    def test_fetch_episodes_filters_processed(self, temp_storage):
        """Test that already processed YouTube episodes are filtered out."""
        source = YouTubeMediaSource(str(temp_storage))

        existing_episode = Episode(
            title="Video 1",
            audio_url="https://www.youtube.com/watch?v=abc123",
            guid="abc123",
            pub_date=datetime(2025, 1, 10),
            description="First video",
            processed=True,
        )

        mock_episodes = [
            existing_episode,
            Episode(
                title="Video 2",
                audio_url="https://www.youtube.com/watch?v=def456",
                guid="def456",
                pub_date=datetime(2025, 1, 9),
                description="Second video",
            ),
        ]

        source.youtube_downloader.get_episodes_from_playlist = Mock(return_value=mock_episodes)

        episodes = source.fetch_episodes(
            url="https://www.youtube.com/@testchannel",
            existing_episodes=[existing_episode],
            last_processed=datetime(2025, 1, 5),
            max_episodes=None,
        )

        # Should only return new episode (Video 2)
        assert len(episodes) == 1
        assert episodes[0].guid == "def456"

    def test_fetch_episodes_respects_max_limit(self, temp_storage):
        """Test that max_episodes limit is respected for YouTube."""
        source = YouTubeMediaSource(str(temp_storage))

        mock_episodes = []
        for i in range(5):
            mock_episodes.append(
                Episode(
                    title=f"Video {i+1}",
                    audio_url=f"https://www.youtube.com/watch?v=vid{i+1}",
                    guid=f"vid{i+1}",
                    pub_date=datetime(2025, 1, i + 1),
                    description=f"Video {i+1} description",
                )
            )

        source.youtube_downloader.get_episodes_from_playlist = Mock(return_value=mock_episodes)

        episodes = source.fetch_episodes(
            url="https://www.youtube.com/@testchannel",
            existing_episodes=[],
            last_processed=None,
            max_episodes=3,
        )

        # Should only return 3 most recent episodes
        assert len(episodes) <= 3

    def test_download_episode(self, temp_storage):
        """Test YouTube episode download delegation."""
        source = YouTubeMediaSource(str(temp_storage))

        episode = Episode(
            title="Video 1",
            audio_url="https://www.youtube.com/watch?v=abc123",
            guid="abc123",
            pub_date=datetime(2025, 1, 10),
            description="First video",
        )

        source.youtube_downloader.download_episode = Mock(return_value="/tmp/storage/video1.m4a")

        result = source.download_episode(episode, "Test Podcast", "/tmp/storage")

        assert result == "/tmp/storage/video1.m4a"
        source.youtube_downloader.download_episode.assert_called_once_with(episode, "Test Podcast")

    def test_fetch_episodes_error_handling(self, temp_storage):
        """Test YouTube episode fetching error handling."""
        source = YouTubeMediaSource(str(temp_storage))

        source.youtube_downloader.get_episodes_from_playlist = Mock(side_effect=Exception("Network error"))

        episodes = source.fetch_episodes(
            url="https://www.youtube.com/@testchannel",
            existing_episodes=[],
            last_processed=None,
            max_episodes=None,
        )

        # Should return empty list on error
        assert len(episodes) == 0


class TestMediaSourceFactory:
    """Test suite for MediaSourceFactory."""

    def test_detect_source_youtube(self, media_factory):
        """Test YouTube URL detection."""
        source = media_factory.detect_source("https://www.youtube.com/watch?v=abc123")

        assert isinstance(source, YouTubeMediaSource)

    def test_detect_source_youtube_playlist(self, media_factory):
        """Test YouTube playlist URL detection."""
        source = media_factory.detect_source("https://www.youtube.com/playlist?list=xyz")

        assert isinstance(source, YouTubeMediaSource)

    def test_detect_source_youtube_channel(self, media_factory):
        """Test YouTube channel URL detection."""
        source = media_factory.detect_source("https://www.youtube.com/@channelname")

        assert isinstance(source, YouTubeMediaSource)

    def test_detect_source_rss(self, media_factory):
        """Test RSS URL detection."""
        source = media_factory.detect_source("https://example.com/feed.xml")

        assert isinstance(source, RSSMediaSource)

    def test_detect_source_apple_podcasts(self, media_factory):
        """Test Apple Podcasts URL detection (defaults to RSS)."""
        source = media_factory.detect_source("https://podcasts.apple.com/us/podcast/id123456")

        assert isinstance(source, RSSMediaSource)

    def test_detect_source_generic_url(self, media_factory):
        """Test generic URL defaults to RSS."""
        source = media_factory.detect_source("https://example.com/some-random-path")

        assert isinstance(source, RSSMediaSource)

    def test_get_youtube_source(self, media_factory):
        """Test getting YouTube source instance."""
        source = media_factory.get_youtube_source()

        assert isinstance(source, YouTubeMediaSource)

    def test_get_rss_source(self, media_factory):
        """Test getting RSS source instance."""
        source = media_factory.get_rss_source()

        assert isinstance(source, RSSMediaSource)

    def test_factory_reuses_instances(self, media_factory):
        """Test that factory reuses source instances."""
        source1 = media_factory.detect_source("https://www.youtube.com/watch?v=abc123")
        source2 = media_factory.detect_source("https://www.youtube.com/watch?v=def456")

        # Should return the same YouTube source instance
        assert source1 is source2

        rss_source1 = media_factory.detect_source("https://example.com/feed1.xml")
        rss_source2 = media_factory.detect_source("https://example.com/feed2.xml")

        # Should return the same RSS source instance
        assert rss_source1 is rss_source2
