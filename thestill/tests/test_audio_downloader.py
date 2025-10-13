"""
Unit tests for AudioDownloader.

Tests download functionality, error handling, and filename sanitization with mocked dependencies.
"""

import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from thestill.core.audio_downloader import AudioDownloader
from thestill.models.podcast import Episode


@pytest.fixture
def temp_storage():
    """Create temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audio_downloader(temp_storage):
    """Create AudioDownloader with temporary storage."""
    downloader = AudioDownloader(str(temp_storage))
    # Mock YouTube downloader to avoid actual network calls
    downloader.youtube_downloader = Mock()
    downloader.youtube_downloader.is_youtube_url = Mock(return_value=False)
    return downloader


@pytest.fixture
def sample_episode():
    """Create sample episode for testing."""
    return Episode(
        title="Test Episode 123",
        audio_url="https://example.com/audio/episode123.mp3",
        guid="test-ep-123",
        pub_date=datetime(2025, 1, 15),
        description="A test episode",
    )


class TestAudioDownloaderInitialization:
    """Test AudioDownloader initialization."""

    def test_init_creates_storage_directory(self, temp_storage):
        """Should create storage directory if it doesn't exist."""
        storage_path = temp_storage / "new_audio_dir"
        assert not storage_path.exists()

        downloader = AudioDownloader(str(storage_path))

        assert downloader.storage_path == storage_path
        assert storage_path.exists()

    def test_init_with_existing_directory(self, temp_storage):
        """Should work with existing directory."""
        downloader = AudioDownloader(str(temp_storage))

        assert downloader.storage_path.exists()
        assert downloader.youtube_downloader is not None


class TestDownloadEpisode:
    """Test download_episode method."""

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_success(self, mock_get, audio_downloader, sample_episode):
        """Should download episode successfully."""
        # Setup mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "1024"}
        mock_response.iter_content = Mock(return_value=[b"chunk1", b"chunk2", b"chunk3"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify
        assert result is not None
        assert Path(result).exists()
        assert "Test_Podcast" in result
        assert "Test_Episode_123" in result
        assert result.endswith(".mp3")

        # Verify file content
        with open(result, "rb") as f:
            content = f.read()
            assert content == b"chunk1chunk2chunk3"

        # Verify network call
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert str(sample_episode.audio_url) in str(call_args)
        assert call_args[1]["timeout"] == 30
        assert "User-Agent" in call_args[1]["headers"]

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_already_exists(self, mock_get, audio_downloader, sample_episode, temp_storage):
        """Should return existing file path without downloading."""
        # Create existing file
        existing_file = temp_storage / "Test_Podcast_Test_Episode_123_12345678.mp3"
        existing_file.write_text("existing content")

        # Mock to match the hash that will be generated
        with patch("thestill.core.audio_downloader.hashlib.md5") as mock_md5:
            mock_hash = Mock()
            mock_hash.hexdigest.return_value = "1234567890abcdef"
            mock_md5.return_value = mock_hash

            result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should return existing file without calling requests
        assert result is not None
        mock_get.assert_not_called()

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_network_error(self, mock_get, audio_downloader, sample_episode):
        """Should handle network errors gracefully after retries."""
        # Setup mock to raise exception (will retry 3 times)
        mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should fail after 3 retry attempts
        assert result is None
        assert mock_get.call_count == 3  # MAX_RETRY_ATTEMPTS

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_timeout(self, mock_get, audio_downloader, sample_episode):
        """Should handle timeout errors after retries."""
        # Setup mock to raise timeout (will retry 3 times)
        mock_get.side_effect = requests.exceptions.Timeout("Request timed out")

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should fail after 3 retry attempts
        assert result is None
        assert mock_get.call_count == 3  # MAX_RETRY_ATTEMPTS

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_http_error(self, mock_get, audio_downloader, sample_episode):
        """Should handle HTTP errors (404, 500, etc) after retries."""
        # Setup mock to raise HTTP error (will retry 3 times)
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
        mock_get.return_value = mock_response

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should fail after 3 retry attempts
        assert result is None
        assert mock_get.call_count == 3  # MAX_RETRY_ATTEMPTS

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_retry_succeeds_on_second_attempt(self, mock_get, audio_downloader, sample_episode):
        """Should succeed if retry attempt succeeds."""
        # Setup mock to fail first time, succeed second time
        mock_response = Mock()
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_content = Mock(return_value=[b"chunk1", b"chunk2"])
        mock_response.raise_for_status = Mock()

        mock_get.side_effect = [
            requests.exceptions.ConnectionError("Network error"),  # First attempt fails
            mock_response,  # Second attempt succeeds
        ]

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should succeed on second attempt
        assert result is not None
        assert Path(result).exists()
        assert mock_get.call_count == 2  # Failed once, succeeded on retry

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_retry_succeeds_on_third_attempt(self, mock_get, audio_downloader, sample_episode):
        """Should succeed if final retry attempt succeeds."""
        # Setup mock to fail twice, succeed on third attempt
        mock_response = Mock()
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_content = Mock(return_value=[b"data"])
        mock_response.raise_for_status = Mock()

        mock_get.side_effect = [
            requests.exceptions.Timeout("Timeout 1"),  # First attempt fails
            requests.exceptions.ConnectionError("Error 2"),  # Second attempt fails
            mock_response,  # Third attempt succeeds
        ]

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should succeed on third attempt
        assert result is not None
        assert Path(result).exists()
        assert mock_get.call_count == 3

    def test_download_youtube_url(self, audio_downloader):
        """Should delegate YouTube URLs to YouTubeDownloader."""
        # Setup
        youtube_episode = Episode(
            title="YouTube Video",
            audio_url="https://www.youtube.com/watch?v=abc123",
            guid="yt-abc123",
            pub_date=datetime(2025, 1, 15),
            description="YouTube video",
        )

        audio_downloader.youtube_downloader.is_youtube_url.return_value = True
        audio_downloader.youtube_downloader.download_episode.return_value = "/path/to/youtube.m4a"

        # Execute
        result = audio_downloader.download_episode(youtube_episode, "YouTube Channel")

        # Verify
        assert result == "/path/to/youtube.m4a"
        audio_downloader.youtube_downloader.is_youtube_url.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc123"
        )
        audio_downloader.youtube_downloader.download_episode.assert_called_once_with(youtube_episode, "YouTube Channel")

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_different_extensions(self, mock_get, audio_downloader):
        """Should handle various audio file extensions."""
        extensions = [".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"]

        for ext in extensions:
            # Create episode with specific extension
            episode = Episode(
                title=f"Episode {ext}",
                audio_url=f"https://example.com/audio/file{ext}",
                guid=f"ep-{ext}",
                pub_date=datetime(2025, 1, 15),
                description="Test",
            )

            # Setup mock
            mock_response = Mock()
            mock_response.headers = {"content-length": "100"}
            mock_response.iter_content = Mock(return_value=[b"data"])
            mock_response.raise_for_status = Mock()
            mock_get.return_value = mock_response

            # Execute
            result = audio_downloader.download_episode(episode, "Podcast")

            # Verify extension is preserved
            assert result.endswith(ext)

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_no_content_length(self, mock_get, audio_downloader, sample_episode):
        """Should handle missing Content-Length header."""
        # Setup mock without content-length
        mock_response = Mock()
        mock_response.headers = {}  # No content-length
        mock_response.iter_content = Mock(return_value=[b"chunk1", b"chunk2"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Execute
        result = audio_downloader.download_episode(sample_episode, "Test Podcast")

        # Verify - should still work
        assert result is not None
        assert Path(result).exists()


class TestFilenameSanitization:
    """Test _sanitize_filename method."""

    def test_sanitize_invalid_characters(self, audio_downloader):
        """Should replace invalid filename characters."""
        invalid_chars = '<>:"/\\|?*'

        for char in invalid_chars:
            filename = f"Test{char}File"
            result = audio_downloader._sanitize_filename(filename)

            assert char not in result
            assert "_" in result

    def test_sanitize_spaces(self, audio_downloader):
        """Should replace spaces with underscores."""
        result = audio_downloader._sanitize_filename("Test File With Spaces")

        assert result == "Test_File_With_Spaces"

    def test_sanitize_long_filename(self, audio_downloader):
        """Should truncate long filenames to 100 characters."""
        long_name = "a" * 200
        result = audio_downloader._sanitize_filename(long_name)

        assert len(result) == 100

    def test_sanitize_unicode(self, audio_downloader):
        """Should handle unicode characters."""
        result = audio_downloader._sanitize_filename("Test 中文 Episode")

        # Should keep printable unicode
        assert "中文" in result or "_" in result  # Depends on unicode handling

    def test_sanitize_empty_string(self, audio_downloader):
        """Should handle empty string."""
        result = audio_downloader._sanitize_filename("")

        assert result == ""


class TestGetFileExtension:
    """Test _get_file_extension method."""

    def test_get_extension_mp3(self, audio_downloader):
        """Should extract .mp3 extension."""
        result = audio_downloader._get_file_extension("/path/to/file.mp3")
        assert result == ".mp3"

    def test_get_extension_m4a(self, audio_downloader):
        """Should extract .m4a extension."""
        result = audio_downloader._get_file_extension("/path/to/file.m4a")
        assert result == ".m4a"

    def test_get_extension_case_insensitive(self, audio_downloader):
        """Should be case insensitive."""
        result = audio_downloader._get_file_extension("/path/to/file.MP3")
        assert result == ".mp3"

    def test_get_extension_default(self, audio_downloader):
        """Should default to .mp3 if no known extension."""
        result = audio_downloader._get_file_extension("/path/to/file.unknown")
        assert result == ".mp3"

    def test_get_extension_no_extension(self, audio_downloader):
        """Should default to .mp3 if no extension."""
        result = audio_downloader._get_file_extension("/path/to/file")
        assert result == ".mp3"


class TestGetFileSize:
    """Test get_file_size method."""

    def test_get_file_size_existing_file(self, audio_downloader, temp_storage):
        """Should return file size for existing file."""
        # Create test file
        test_file = temp_storage / "test.txt"
        test_file.write_text("Hello, World!")

        # Execute
        size = audio_downloader.get_file_size(str(test_file))

        # Verify
        assert size == 13  # "Hello, World!" is 13 bytes

    def test_get_file_size_nonexistent_file(self, audio_downloader):
        """Should return 0 for nonexistent file."""
        size = audio_downloader.get_file_size("/nonexistent/file.txt")

        assert size == 0

    def test_get_file_size_invalid_path(self, audio_downloader):
        """Should return 0 for invalid path."""
        size = audio_downloader.get_file_size(None)

        assert size == 0


class TestCleanupOldFiles:
    """Test cleanup_old_files method."""

    def test_cleanup_removes_old_files(self, audio_downloader, temp_storage):
        """Should remove files older than specified days."""
        # Create old file
        old_file = temp_storage / "old_file.mp3"
        old_file.write_text("old content")

        # Set modification time to 60 days ago
        old_time = time.time() - (60 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        # Create recent file
        recent_file = temp_storage / "recent_file.mp3"
        recent_file.write_text("recent content")

        # Execute cleanup (30 days)
        audio_downloader.cleanup_old_files(days=30)

        # Verify
        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_no_files_removed(self, audio_downloader, temp_storage):
        """Should not remove recent files."""
        # Create recent file
        recent_file = temp_storage / "recent_file.mp3"
        recent_file.write_text("recent content")

        # Execute cleanup
        audio_downloader.cleanup_old_files(days=30)

        # Verify
        assert recent_file.exists()

    def test_cleanup_handles_errors(self, audio_downloader, temp_storage):
        """Should handle errors gracefully during cleanup."""
        # Create file
        test_file = temp_storage / "test.mp3"
        test_file.write_text("content")

        # Set old time
        old_time = time.time() - (60 * 24 * 60 * 60)
        os.utime(test_file, (old_time, old_time))

        # Mock unlink to raise exception
        with patch.object(Path, "unlink", side_effect=PermissionError("Access denied")):
            # Should not raise exception
            audio_downloader.cleanup_old_files(days=30)

        # File still exists because unlink failed
        assert test_file.exists()

    def test_cleanup_dry_run_mode(self, audio_downloader, temp_storage):
        """Should preview cleanup without deleting files in dry-run mode."""
        # Create old file
        old_file = temp_storage / "old_file.mp3"
        old_file.write_text("old content")

        # Set modification time to 60 days ago
        old_time = time.time() - (60 * 24 * 60 * 60)
        os.utime(old_file, (old_time, old_time))

        # Create recent file
        recent_file = temp_storage / "recent_file.mp3"
        recent_file.write_text("recent content")

        # Execute cleanup with dry-run
        count = audio_downloader.cleanup_old_files(days=30, dry_run=True)

        # Verify - files should still exist
        assert old_file.exists()
        assert recent_file.exists()
        # Should have counted the old file
        assert count == 1

    def test_cleanup_dry_run_logs_correctly(self, audio_downloader, temp_storage, caplog):
        """Should log correct messages in dry-run mode."""
        import logging

        # Create multiple old files
        old_file1 = temp_storage / "old_file1.mp3"
        old_file1.write_text("content1")
        old_file2 = temp_storage / "old_file2.mp3"
        old_file2.write_text("content2")

        # Set modification time to 60 days ago
        old_time = time.time() - (60 * 24 * 60 * 60)
        os.utime(old_file1, (old_time, old_time))
        os.utime(old_file2, (old_time, old_time))

        # Execute cleanup with dry-run
        with caplog.at_level(logging.INFO):
            count = audio_downloader.cleanup_old_files(days=30, dry_run=True)

        # Verify return count
        assert count == 2

        # Verify log messages contain "Would delete"
        log_messages = [record.message for record in caplog.records]
        assert any("Would delete: old_file1.mp3" in msg for msg in log_messages)
        assert any("Would delete: old_file2.mp3" in msg for msg in log_messages)
        assert any("Would clean up 2 old audio files (dry-run)" in msg for msg in log_messages)

    def test_cleanup_returns_count(self, audio_downloader, temp_storage):
        """Should return correct count of deleted files."""
        # Create 3 old files
        for i in range(3):
            old_file = temp_storage / f"old_file{i}.mp3"
            old_file.write_text(f"content{i}")
            old_time = time.time() - (60 * 24 * 60 * 60)
            os.utime(old_file, (old_time, old_time))

        # Execute cleanup
        count = audio_downloader.cleanup_old_files(days=30)

        # Verify count
        assert count == 3

        # Verify files are deleted
        for i in range(3):
            assert not (temp_storage / f"old_file{i}.mp3").exists()


class TestEdgeCases:
    """Test edge cases and error handling."""

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_with_special_characters_in_title(self, mock_get, audio_downloader):
        """Should handle special characters in podcast/episode titles."""
        episode = Episode(
            title="Episode: Test / Part 1 <New>",
            audio_url="https://example.com/audio.mp3",
            guid="special-ep",
            pub_date=datetime(2025, 1, 15),
            description="Test",
        )

        # Setup mock
        mock_response = Mock()
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_content = Mock(return_value=[b"data"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Execute
        result = audio_downloader.download_episode(episode, "Podcast: The <Best> / Show")

        # Verify - should work without errors
        assert result is not None
        assert Path(result).exists()
        # Special characters should be sanitized in filename (not full path)
        filename = Path(result).name
        assert ":" not in filename
        assert "/" not in filename
        assert "<" not in filename
        assert ">" not in filename

    @patch("thestill.core.audio_downloader.requests.get")
    def test_download_file_write_error(self, mock_get, audio_downloader, temp_storage):
        """Should handle file write errors gracefully."""
        episode = Episode(
            title="Test Episode",
            audio_url="https://example.com/audio.mp3",
            guid="write-error-ep",
            pub_date=datetime(2025, 1, 15),
            description="Test",
        )

        # Setup mock
        mock_response = Mock()
        mock_response.headers = {"content-length": "100"}
        mock_response.iter_content = Mock(return_value=[b"data"])
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        # Mock open to raise exception
        with patch("builtins.open", side_effect=IOError("Disk full")):
            result = audio_downloader.download_episode(episode, "Test Podcast")

        # Verify
        assert result is None
