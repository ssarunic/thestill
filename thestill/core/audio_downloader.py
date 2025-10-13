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

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models.podcast import Episode
from .youtube_downloader import YouTubeDownloader

logger = logging.getLogger(__name__)

# Network Configuration Constants
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30  # Timeout for HTTP requests
DEFAULT_CHUNK_SIZE_BYTES = 8192  # 8KB chunks for streaming downloads

# Retry Configuration Constants
MAX_RETRY_ATTEMPTS = 3  # Maximum number of download retry attempts
RETRY_WAIT_MIN_SECONDS = 1  # Minimum wait time between retries (exponential backoff start)
RETRY_WAIT_MAX_SECONDS = 60  # Maximum wait time between retries (exponential backoff cap)
RETRY_WAIT_MULTIPLIER = 1  # Multiplier for exponential backoff (2^attempt * multiplier)

# Filename Constants
MAX_FILENAME_LENGTH = 100  # Maximum characters for sanitized filenames
URL_HASH_LENGTH = 8  # Number of characters from MD5 hash to include in filename


class AudioDownloader:
    """
    Downloads podcast audio files from RSS feeds and YouTube.

    Attributes:
        storage_path: Directory where downloaded audio files are stored
        youtube_downloader: Handler for YouTube-specific downloads
    """

    def __init__(self, storage_path: str = "./data/original_audio") -> None:
        """
        Initialize audio downloader.

        Args:
            storage_path: Directory path for storing downloaded audio files
        """
        self.storage_path: Path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.youtube_downloader: YouTubeDownloader = YouTubeDownloader(storage_path)

    def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
        """
        Download episode audio file to original_audio/ directory.

        Returns:
            Path to downloaded audio file, or None if download failed
        """
        try:
            # Check if this is a YouTube URL
            if self.youtube_downloader.is_youtube_url(str(episode.audio_url)):
                return self.youtube_downloader.download_episode(episode, podcast_title)

            # Handle regular audio URLs
            safe_podcast_title = self._sanitize_filename(podcast_title)
            safe_episode_title = self._sanitize_filename(episode.title)

            url_hash = hashlib.md5(str(episode.audio_url).encode()).hexdigest()[:URL_HASH_LENGTH]

            parsed_url = urlparse(str(episode.audio_url))
            extension = self._get_file_extension(parsed_url.path)

            filename = f"{safe_podcast_title}_{safe_episode_title}_{url_hash}{extension}"
            local_path = self.storage_path / filename

            if local_path.exists():
                logger.info(f"File already exists: {filename}")
                return str(local_path)

            logger.info(f"Downloading episode: {episode.title}")

            # Use retry-enabled download method for network operations
            self._download_with_retry(str(episode.audio_url), local_path)

            logger.info(f"Download completed: {filename}")
            return str(local_path)

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error downloading {episode.title}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading {episode.title}: {e}")
            return None

    @retry(
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_WAIT_MULTIPLIER, min=RETRY_WAIT_MIN_SECONDS, max=RETRY_WAIT_MAX_SECONDS),
        retry=retry_if_exception_type(requests.exceptions.RequestException),
        reraise=True,
    )
    def _download_with_retry(self, url: str, local_path: Path) -> None:
        """
        Download file from URL with automatic retry on network errors.

        Uses exponential backoff: waits 1s, 2s, 4s between attempts.
        Retries up to 3 times for transient network errors.

        Args:
            url: Source URL to download from
            local_path: Destination file path

        Raises:
            requests.exceptions.RequestException: If download fails after all retries
        """
        response = requests.get(
            url,
            stream=True,
            headers={"User-Agent": "thestill.ai/1.0"},
            timeout=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0

        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE_BYTES):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = (downloaded / total_size) * 100
                        # Use \r for same-line progress updates to stderr
                        logger.info(f"\rProgress: {progress:.1f}%")

    def get_file_size(self, file_path: str) -> int:
        """Get file size in bytes"""
        try:
            return os.path.getsize(file_path)
        except (OSError, FileNotFoundError, TypeError) as e:
            logger.debug(f"Failed to get file size for {file_path}: {e}")
            return 0

    def cleanup_old_files(self, days: int = 30) -> None:
        """
        Remove audio files older than specified days.

        Args:
            days: Number of days after which files are considered old
        """
        import time

        cutoff_time = time.time() - (days * 24 * 60 * 60)

        removed_count = 0
        for file_path in self.storage_path.glob("*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    removed_count += 1
                except Exception as e:
                    logger.error(f"Error removing {file_path}: {e}")

        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old audio files")

    def _sanitize_filename(self, filename: str) -> str:
        """Remove/replace invalid filename characters"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, "_")

        filename = filename.replace(" ", "_")
        filename = "".join(c for c in filename if c.isprintable())

        return filename[:MAX_FILENAME_LENGTH]

    def _get_file_extension(self, url_path: str) -> str:
        """Extract file extension from URL path"""
        extensions = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}

        for ext in extensions:
            if url_path.lower().endswith(ext):
                return ext

        return ".mp3"
