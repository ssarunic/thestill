# Copyright 2025-2026 Thestill
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
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..models.podcast import Episode, Podcast
from ..utils.audio_integrity import InvalidAudioFile, assert_audio_file
from ..utils.url_guard import UnsafeURLError, guarded_redirect_fetch
from .media_source import MediaSourceFactory

logger = structlog.get_logger(__name__)


class DownloadError(Exception):
    """Raised when episode download fails."""

    pass


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
    # Default cap on any single download when the caller did not specify one.
    # Matches Config.max_audio_bytes so CLI / web / MCP see the same ceiling.
    _DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

    # Retry configuration
    _MAX_RETRY_ATTEMPTS = 3  # Maximum number of download retry attempts
    _RETRY_WAIT_MIN_SECONDS = 1  # Minimum wait time between retries
    _RETRY_WAIT_MAX_SECONDS = 60  # Maximum wait time between retries
    _RETRY_WAIT_MULTIPLIER = 1  # Multiplier for exponential backoff

    # Filename configuration
    _MAX_FILENAME_LENGTH = 100  # Maximum characters for sanitized filenames
    _URL_HASH_LENGTH = 8  # Number of characters from MD5 hash to include

    def __init__(
        self,
        storage_path: str = "./data/original_audio",
        *,
        max_bytes: Optional[int] = None,
    ) -> None:
        """
        Initialize audio downloader.

        Args:
            storage_path: Directory path for storing downloaded audio files.
            max_bytes: Hard ceiling on any single download. Callers should
                thread ``Config.max_audio_bytes`` through; if omitted we
                use ``_DEFAULT_MAX_BYTES`` so bare instantiation in tests
                still enforces the cap.
        """
        self.storage_path: Path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.media_source_factory: MediaSourceFactory = MediaSourceFactory(storage_path)
        self.max_bytes = int(max_bytes) if max_bytes is not None else self._DEFAULT_MAX_BYTES

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
                logger.error("youtube_download_failed", episode_title=episode.title, reason="no_fallback_available")
                raise DownloadError(f"YouTube download failed for '{episode.title}' (yt-dlp error)")

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
                logger.info("audio_file_exists", relative_path=relative_path)
                return relative_path

            logger.info("downloading_episode", episode_title=episode.title, relative_path=relative_path)

            # Use retry-enabled download method for network operations
            self._download_with_retry(str(episode.audio_url), local_path)

            logger.info("download_completed", relative_path=relative_path, episode_title=episode.title)
            return relative_path

        except requests.exceptions.RequestException as e:
            logger.error("network_error_downloading", episode_title=episode.title, error=str(e), exc_info=True)
            raise DownloadError(f"Network error downloading '{episode.title}': {e}") from e
        except Exception as e:
            logger.error("error_downloading_episode", episode_title=episode.title, error=str(e), exc_info=True)
            raise DownloadError(f"Failed to download '{episode.title}': {e}") from e

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
            DownloadError: If the URL targets a private/loopback/cloud-metadata address.
        """
        # Block SSRF targets and re-validate any 3xx
        # redirect so a public URL cannot 302 into a private one.
        try:
            response = guarded_redirect_fetch(
                url,
                requests.get,
                stream=True,
                headers={"User-Agent": "Thestill/1.0"},
                timeout=self._DEFAULT_TIMEOUT_SECONDS,
            )
        except UnsafeURLError as exc:
            raise DownloadError(f"Refusing to download from unsafe URL: {exc}") from exc
        response.raise_for_status()

        # Pre-check content-length, then enforce a
        # cumulative cap while streaming so a lying/missing header cannot
        # defeat the limit.
        try:
            advertised = int(response.headers.get("content-length", 0))
        except ValueError:
            advertised = 0
        if advertised and advertised > self.max_bytes:
            raise DownloadError(
                f"Refusing download: server advertised {advertised} bytes, " f"cap is {self.max_bytes} bytes"
            )

        downloaded = 0
        try:
            with open(local_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=self._CHUNK_SIZE_BYTES):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > self.max_bytes:
                            raise DownloadError(
                                f"Refusing download: stream exceeded cap " f"({self.max_bytes} bytes) while reading"
                            )
        except Exception:
            # Leave no half-written file behind if we bail on cap/IO error;
            # callers re-queue downloads and a partial .mp3 is worse than
            # nothing (ffprobe would happily "parse" it).
            try:
                local_path.unlink(missing_ok=True)
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
            raise

        # Magic-byte integrity check. Rejects zip bombs,
        # HTML error pages, and polyglot payloads before ffmpeg gets a chance.
        try:
            codec = assert_audio_file(local_path)
            logger.debug("audio_codec_detected", codec=codec, path=str(local_path))
        except InvalidAudioFile as exc:
            local_path.unlink(missing_ok=True)
            raise DownloadError(f"Downloaded file failed integrity check: {exc}") from exc

    def delete_audio_file(self, episode: Episode) -> bool:
        """
        Delete the original audio file for an episode.

        Args:
            episode: Episode whose audio file should be deleted

        Returns:
            True if file was deleted or didn't exist, False on error
        """
        if not episode.audio_path:
            logger.debug("no_audio_path", episode_title=episode.title)
            return True

        file_path = self.storage_path / episode.audio_path

        if not file_path.exists():
            logger.debug("audio_file_already_deleted", audio_path=episode.audio_path)
            return True

        try:
            file_path.unlink()
            logger.info("deleted_original_audio", audio_path=episode.audio_path)
            return True
        except Exception as e:
            logger.error("error_deleting_audio_file", audio_path=episode.audio_path, error=str(e), exc_info=True)
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
                    logger.info("would_delete_old_file", filename=file_path.name)
                    removed_count += 1
                else:
                    try:
                        file_path.unlink()
                        logger.info("deleted_old_file", filename=file_path.name)
                        removed_count += 1
                    except Exception as e:
                        logger.error("error_removing_file", filepath=str(file_path), error=str(e), exc_info=True)

        if removed_count > 0:
            if dry_run:
                logger.info("cleanup_summary", files_count=removed_count, mode="dry_run")
            else:
                logger.info("cleanup_completed", files_count=removed_count)

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
