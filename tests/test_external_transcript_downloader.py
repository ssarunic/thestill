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

"""Unit tests for external transcript downloader."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from thestill.core.external_transcript_downloader import ExternalTranscriptDownloader
from thestill.models.podcast import Episode, Podcast, TranscriptLink
from thestill.utils.path_manager import PathManager


@pytest.fixture
def path_manager(tmp_path):
    """Create a PathManager with temp directory."""
    return PathManager(storage_path=str(tmp_path))


@pytest.fixture
def mock_repository():
    """Create a mock repository."""
    return MagicMock()


@pytest.fixture
def downloader(mock_repository, path_manager):
    """Create an ExternalTranscriptDownloader instance."""
    return ExternalTranscriptDownloader(
        repository=mock_repository,
        path_manager=path_manager,
    )


@pytest.fixture
def sample_transcript_links():
    """Create sample transcript links for testing."""
    return [
        TranscriptLink(
            id=1,
            episode_id="ep-123",
            url="https://example.com/transcript.srt",
            mime_type="application/x-subrip",
            language="en",
        ),
        TranscriptLink(
            id=2,
            episode_id="ep-123",
            url="https://example.com/transcript.vtt",
            mime_type="text/vtt",
            language="en",
        ),
        TranscriptLink(
            id=3,
            episode_id="ep-123",
            url="https://example.com/transcript.json",
            mime_type="application/json",
            language="en",
        ),
    ]


class TestDownloadAllForEpisode:
    """Tests for download_all_for_episode method."""

    def test_no_links_returns_empty(self, downloader, mock_repository):
        """Test that no transcript links returns empty dict."""
        mock_repository.get_transcript_links.return_value = []

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        assert result == {}
        mock_repository.get_transcript_links.assert_called_once_with("ep-123")

    def test_all_already_downloaded_returns_empty(self, downloader, mock_repository, sample_transcript_links):
        """Test that already-downloaded links are skipped."""
        # Mark all as already downloaded
        for link in sample_transcript_links:
            link.downloaded_path = f"/path/to/{link.format_extension}"

        mock_repository.get_transcript_links.return_value = sample_transcript_links

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        assert result == {}

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_successful_download(self, mock_get, downloader, mock_repository, path_manager):
        """Test successful transcript download."""
        link = TranscriptLink(
            id=1,
            episode_id="ep-123",
            url="https://example.com/transcript.srt",
            mime_type="application/x-subrip",
            language="en",
        )
        mock_repository.get_transcript_links.return_value = [link]

        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.content = b"1\n00:00:00,000 --> 00:00:05,000\nHello world\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        assert "srt" in result
        assert "test-episode.srt" in result["srt"]

        # Verify file was written
        file_path = path_manager.external_transcript_file("test-podcast", "test-episode", "srt")
        assert file_path.exists()
        assert b"Hello world" in file_path.read_bytes()

        # Verify database was updated
        mock_repository.mark_transcript_downloaded.assert_called_once()

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_download_multiple_formats(
        self, mock_get, downloader, mock_repository, sample_transcript_links, path_manager
    ):
        """Test downloading multiple transcript formats."""
        mock_repository.get_transcript_links.return_value = sample_transcript_links

        # Mock HTTP responses for each format
        def mock_response_factory(url, **kwargs):
            response = MagicMock()
            response.raise_for_status = MagicMock()
            if "srt" in url:
                response.content = b"SRT content"
            elif "vtt" in url:
                response.content = b"VTT content"
            elif "json" in url:
                response.content = b"JSON content"
            return response

        mock_get.side_effect = mock_response_factory

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        assert len(result) == 3
        assert "srt" in result
        assert "vtt" in result
        assert "json" in result

        # Verify all files were created
        for ext in ["srt", "vtt", "json"]:
            file_path = path_manager.external_transcript_file("test-podcast", "test-episode", ext)
            assert file_path.exists()

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_download_failure_continues(
        self, mock_get, downloader, mock_repository, sample_transcript_links, path_manager
    ):
        """Test that one download failure doesn't stop others."""
        mock_repository.get_transcript_links.return_value = sample_transcript_links

        # First request fails, others succeed
        def mock_response_factory(url, **kwargs):
            if "srt" in url:
                raise requests.exceptions.Timeout("Connection timed out")
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.content = b"Content"
            return response

        mock_get.side_effect = mock_response_factory

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        # SRT failed but VTT and JSON should succeed
        assert "srt" not in result
        assert "vtt" in result
        assert "json" in result

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_skip_existing_file(self, mock_get, downloader, mock_repository, path_manager):
        """Test that existing files are skipped."""
        link = TranscriptLink(
            id=1,
            episode_id="ep-123",
            url="https://example.com/transcript.srt",
            mime_type="application/x-subrip",
        )
        mock_repository.get_transcript_links.return_value = [link]

        # Pre-create the file
        file_path = path_manager.external_transcript_file("test-podcast", "test-episode", "srt")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("Existing content")

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        # Should return the existing file path without making HTTP request
        assert "srt" in result
        mock_get.assert_not_called()

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_http_error_handling(self, mock_get, downloader, mock_repository):
        """Test handling of HTTP errors."""
        link = TranscriptLink(
            id=1,
            episode_id="ep-123",
            url="https://example.com/transcript.srt",
            mime_type="application/x-subrip",
        )
        mock_repository.get_transcript_links.return_value = [link]

        # Mock 404 response
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        result = downloader.download_all_for_episode(
            episode_id="ep-123",
            podcast_slug="test-podcast",
            episode_slug="test-episode",
        )

        assert result == {}


class TestDownloadAllPending:
    """Tests for download_all_pending method."""

    def test_no_pending_episodes(self, downloader, mock_repository):
        """Test when there are no episodes with pending downloads."""
        mock_repository.get_episodes_with_undownloaded_transcript_links.return_value = []

        result = downloader.download_all_pending()

        assert result == 0

    @patch("thestill.core.external_transcript_downloader.requests.get")
    def test_process_multiple_episodes(self, mock_get, downloader, mock_repository, path_manager):
        """Test processing multiple episodes with pending downloads."""
        episode1 = Episode(
            id="ep-1",
            external_id="guid-1",
            title="Episode 1",
            slug="episode-1",
            description="Test episode 1",
            audio_url="https://example.com/ep1.mp3",
        )
        episode2 = Episode(
            id="ep-2",
            external_id="guid-2",
            title="Episode 2",
            slug="episode-2",
            description="Test episode 2",
            audio_url="https://example.com/ep2.mp3",
        )

        podcast = Podcast(
            id="pod-1",
            title="Test Podcast",
            slug="test-podcast",
            description="Test podcast description",
            rss_url="https://example.com/rss",
        )

        link1 = TranscriptLink(
            id=1,
            episode_id="ep-1",
            url="https://example.com/ep1.srt",
            mime_type="application/x-subrip",
        )
        link2 = TranscriptLink(
            id=2,
            episode_id="ep-2",
            url="https://example.com/ep2.srt",
            mime_type="application/x-subrip",
        )

        mock_repository.get_episodes_with_undownloaded_transcript_links.return_value = [
            (episode1, [link1]),
            (episode2, [link2]),
        ]
        mock_repository.get_podcast_for_episode.return_value = podcast
        mock_repository.get_transcript_links.side_effect = [[link1], [link2]]

        # Mock HTTP responses
        mock_response = MagicMock()
        mock_response.content = b"SRT content"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = downloader.download_all_pending()

        assert result == 2

    def test_max_episodes_limit(self, downloader, mock_repository):
        """Test that max_episodes limit is respected."""
        episodes = [
            (
                Episode(
                    id=f"ep-{i}",
                    external_id=f"guid-{i}",
                    title=f"Episode {i}",
                    slug=f"episode-{i}",
                    description=f"Test episode {i}",
                    audio_url=f"https://example.com/ep{i}.mp3",
                ),
                [],
            )
            for i in range(10)
        ]

        mock_repository.get_episodes_with_undownloaded_transcript_links.return_value = episodes
        mock_repository.get_podcast_for_episode.return_value = Podcast(
            id="pod-1",
            title="Test",
            slug="test",
            description="Test podcast",
            rss_url="https://example.com/rss",
        )
        mock_repository.get_transcript_links.return_value = []

        result = downloader.download_all_pending(max_episodes=3)

        assert result == 3

    def test_filter_by_podcast_id(self, downloader, mock_repository):
        """Test filtering by podcast_id."""
        mock_repository.get_episodes_with_undownloaded_transcript_links.return_value = []

        downloader.download_all_pending(podcast_id="pod-123")

        mock_repository.get_episodes_with_undownloaded_transcript_links.assert_called_once_with("pod-123")

    def test_skip_episode_without_podcast(self, downloader, mock_repository):
        """Test that episodes without podcast are skipped."""
        episode = Episode(
            id="ep-1",
            external_id="guid-1",
            title="Orphan Episode",
            slug="orphan-episode",
            description="Test orphan episode",
            audio_url="https://example.com/orphan.mp3",
        )

        mock_repository.get_episodes_with_undownloaded_transcript_links.return_value = [(episode, [])]
        mock_repository.get_podcast_for_episode.return_value = None

        result = downloader.download_all_pending()

        assert result == 0


class TestTranscriptLinkFormatExtension:
    """Tests for TranscriptLink format_extension property."""

    def test_srt_extension(self):
        """Test SRT MIME type returns srt extension."""
        link = TranscriptLink(
            url="https://example.com/t.srt",
            mime_type="application/x-subrip",
        )
        assert link.format_extension == "srt"

    def test_srt_alt_extension(self):
        """Test alternative SRT MIME type."""
        link = TranscriptLink(
            url="https://example.com/t.srt",
            mime_type="application/srt",
        )
        assert link.format_extension == "srt"

    def test_vtt_extension(self):
        """Test VTT MIME type returns vtt extension."""
        link = TranscriptLink(
            url="https://example.com/t.vtt",
            mime_type="text/vtt",
        )
        assert link.format_extension == "vtt"

    def test_json_extension(self):
        """Test JSON MIME type returns json extension."""
        link = TranscriptLink(
            url="https://example.com/t.json",
            mime_type="application/json",
        )
        assert link.format_extension == "json"

    def test_txt_extension(self):
        """Test plain text MIME type returns txt extension."""
        link = TranscriptLink(
            url="https://example.com/t.txt",
            mime_type="text/plain",
        )
        assert link.format_extension == "txt"

    def test_html_extension(self):
        """Test HTML MIME type returns html extension."""
        link = TranscriptLink(
            url="https://example.com/t.html",
            mime_type="text/html",
        )
        assert link.format_extension == "html"

    def test_unknown_mime_type_defaults_to_txt(self):
        """Test unknown MIME type defaults to txt."""
        link = TranscriptLink(
            url="https://example.com/t.unknown",
            mime_type="application/octet-stream",
        )
        assert link.format_extension == "txt"
