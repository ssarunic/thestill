# Copyright 2025 thestill.me
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

from ..models.podcast import Episode, Podcast
from .media_source import MediaSourceFactory

logger = logging.getLogger(__name__)


class AudioDownloader:
    """
    Downloads podcast audio files from RSS feeds and YouTube.

    Attributes:
        storage_path: Directory where downloaded audio files are stored
        media_source_factory: Factory for detecting and handling different media sources
    """

    # Network configuration
    _DEFAULT_TIMEOUT_SECONDS = 30  # Timeout for HTTP requests
    _CHUNK_SIZE_BYTES = 8192  # 8KB chunks for streaming downloads

    # Retry configuration
    _MAX_RETRY_ATTEMPTS = 3  # Maximum number of download retry attempts
    _RETRY_WAIT_MIN_SECONDS = 1  # Minimum wait time between retries
    _RETRY_WAIT_MAX_SECONDS = 60  # Maximum wait time between retries
    _RETRY_WAIT_MULTIPLIER = 1  # Multiplier for exponential backoff

    # Filename configuration
    _MAX_FILENAME_LENGTH = 100  # Maximum characters for sanitized filenames
    _URL_HASH_LENGTH = 8  # Number of characters from MD5 hash to include

    def __init__(self, storage_path: str = "./data/original_audio") -> None:
        """
        Initialize audio downloader.

        Args:
            storage_path: Directory path for storing downloaded audio files
        """
        self.storage_path: Path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.media_source_factory: MediaSourceFactory = MediaSourceFactory(storage_path)

    def download_episode(
        self,
        episode: Episode,
        podcast: Podcast,
    ) -> Optional[str]:
        """
        Download episode audio file to original_audio/ directory.

        Args:
            episode: Episode to download
            podcast: Podcast the episode belongs to

        Returns:
            Path to downloaded audio file, or None if download failed
        """
        try:
            # Detect source type and delegate if needed
            source = self.media_source_factory.detect_source(str(episode.audio_url))
            source_result = source.download_episode(episode, podcast.title, str(self.storage_path))

            # If source handled download (e.g., YouTube), return result
            # Note: YouTube source will return None on failure, but we should NOT
            # fall back to HTTP download for YouTube URLs as they require yt-dlp
            if source_result is not None:
                return source_result

            # Check if this is a YouTube URL - if so, don't try HTTP fallback
            from .youtube_downloader import YouTubeDownloader

            if YouTubeDownloader.is_youtube_url(str(episode.audio_url)):
                logger.error(f"YouTube download failed for {episode.title}, no fallback available")
                return None

            # Handle standard HTTP downloads (RSS feeds)
            # Use slugs for filename generation (fall back to sanitized titles for backwards compatibility)
            safe_podcast = podcast.slug or self._sanitize_filename(podcast.title)
            safe_episode = episode.slug or self._sanitize_filename(episode.title)

            url_hash = hashlib.md5(str(episode.audio_url).encode()).hexdigest()[: self._URL_HASH_LENGTH]

            parsed_url = urlparse(str(episode.audio_url))
            extension = self._get_file_extension(parsed_url.path)

            # Create podcast subdirectory
            podcast_dir = self.storage_path / safe_podcast
            podcast_dir.mkdir(parents=True, exist_ok=True)

            # Filename format: {episode_slug}_{hash}.ext (podcast slug is in directory)
            filename = f"{safe_episode}_{url_hash}{extension}"
            local_path = podcast_dir / filename

            # Database stores relative path: {podcast_slug}/{filename}
            relative_path = f"{safe_podcast}/{filename}"

            if local_path.exists():
                logger.info(f"File already exists: {relative_path}")
                return relative_path

            logger.info(f"Downloading episode: {episode.title}")

            # Use retry-enabled download method for network operations
            self._download_with_retry(str(episode.audio_url), local_path)

            logger.info(f"Download completed: {relative_path}")
            return relative_path

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error downloading {episode.title}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error downloading {episode.title}: {e}")
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=60),
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
            headers={"User-Agent": "thestill.me/1.0"},
            timeout=self._DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0

        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=self._CHUNK_SIZE_BYTES):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    # Progress updates are handled by CLI progress bar
                    # Removed per-chunk logging to avoid terminal spam

    def delete_audio_file(self, episode: Episode) -> bool:
        """
        Delete the original audio file for an episode.

        Args:
            episode: Episode whose audio file should be deleted

        Returns:
            True if file was deleted or didn't exist, False on error
        """
        if not episode.audio_path:
            logger.debug(f"No audio_path set for episode {episode.title}")
            return True

        file_path = self.storage_path / episode.audio_path

        if not file_path.exists():
            logger.debug(f"Audio file already deleted: {episode.audio_path}")
            return True

        try:
            file_path.unlink()
            logger.info(f"Deleted original audio: {episode.audio_path}")
            return True
        except Exception as e:
            logger.error(f"Error deleting audio file {episode.audio_path}: {e}")
            return False

    def cleanup_old_files(self, days: int = 30, dry_run: bool = False) -> int:
        """
        Remove audio files older than specified days.

        Args:
            days: Number of days after which files are considered old
            dry_run: If True, only log what would be deleted without actually deleting

        Returns:
            Number of files that were deleted (or would be deleted in dry-run mode)
        """
        import time

        cutoff_time = time.time() - (days * 24 * 60 * 60)

        removed_count = 0
        for file_path in self.storage_path.glob("**/*"):
            if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                if dry_run:
                    logger.info(f"Would delete: {file_path.name}")
                    removed_count += 1
                else:
                    try:
                        file_path.unlink()
                        logger.info(f"Deleted old file: {file_path.name}")
                        removed_count += 1
                    except Exception as e:
                        logger.error(f"Error removing {file_path}: {e}")

        if removed_count > 0:
            if dry_run:
                logger.info(f"Would clean up {removed_count} old audio files (dry-run)")
            else:
                logger.info(f"Cleaned up {removed_count} old audio files")

        return removed_count

    def _sanitize_filename(self, filename: str) -> str:
        """Remove/replace invalid filename characters"""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, "_")

        filename = filename.replace(" ", "_")
        filename = "".join(c for c in filename if c.isprintable())

        return filename[: self._MAX_FILENAME_LENGTH]

    def _get_file_extension(self, url_path: str) -> str:
        """Extract file extension from URL path"""
        extensions = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}

        for ext in extensions:
            if url_path.lower().endswith(ext):
                return ext

        return ".mp3"
