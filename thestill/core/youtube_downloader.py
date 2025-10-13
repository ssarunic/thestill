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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yt_dlp

from ..models.podcast import Episode

logger = logging.getLogger(__name__)


class YouTubeDownloader:
    """
    Handle YouTube podcast/playlist downloads using yt-dlp.

    Attributes:
        storage_path: Directory where downloaded audio files are stored
    """

    def __init__(self, storage_path: str = "./data/audio") -> None:
        """
        Initialize YouTube downloader.

        Args:
            storage_path: Directory path for storing downloaded audio files
        """
        self.storage_path: Path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def is_youtube_url(url: str) -> bool:
        """
        Check if URL is a YouTube video or playlist.

        Args:
            url: URL to check

        Returns:
            True if URL is a YouTube URL, False otherwise
        """
        youtube_patterns = [
            r"youtube\.com/watch",
            r"youtube\.com/playlist",
            r"youtube\.com/@[\w-]+",
            r"youtube\.com/channel/",
            r"youtube\.com/c/",
            r"youtu\.be/",
        ]
        return any(re.search(pattern, url) for pattern in youtube_patterns)

    def extract_playlist_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract playlist/channel information from YouTube URL.

        Args:
            url: YouTube playlist or channel URL

        Returns:
            Dictionary with playlist information or None if extraction fails
        """
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,  # Don't download, just get metadata
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    return None

                # Handle both playlists and single videos
                if info.get("_type") == "playlist":
                    return {
                        "title": info.get("title", "Unknown YouTube Playlist"),
                        "description": info.get("description", ""),
                        "uploader": info.get("uploader", info.get("channel", "")),
                        "entries": info.get("entries", []),
                        "url": url,
                    }
                # Single video - treat as a playlist with one entry
                return {
                    "title": info.get("uploader", "Unknown YouTube Channel"),
                    "description": f"Single video: {info.get('title', '')}",
                    "uploader": info.get("uploader", info.get("channel", "")),
                    "entries": [info],
                    "url": url,
                }

        except Exception as e:
            logger.error(f"Error extracting YouTube info from {url}: {e}")
            return None

    def get_episodes_from_playlist(self, url: str) -> List[Episode]:
        """
        Get list of episodes from a YouTube playlist/channel.

        Args:
            url: YouTube playlist or channel URL

        Returns:
            List of Episode objects parsed from YouTube videos
        """
        try:
            from datetime import datetime

            logger.info(f"Fetching YouTube episodes from: {url}")

            # For channels, convert to /videos URL to get actual videos
            if "/@" in url or "/channel/" in url or "/c/" in url:
                # Ensure we're getting the videos tab
                if not url.endswith("/videos"):
                    url = url.rstrip("/") + "/videos"
                logger.info(f"Using videos URL: {url}")

            # Use flat extraction first to get video IDs quickly
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": "in_playlist",  # Fast extraction
                "playlistend": 10,  # Only get 10 most recent videos
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    return []

                episodes = []
                entries = info.get("entries", []) if info.get("_type") == "playlist" else [info]

                logger.info(f"Found {len(entries)} videos")

                for entry in entries:
                    if not entry:
                        continue

                    # Get video ID - handle different formats
                    video_id = entry.get("id") or entry.get("url", "")

                    # Skip if it's not a valid video ID (e.g., channel tabs)
                    if not video_id or len(video_id) != 11 or video_id.startswith("UC"):
                        continue

                    # Construct full video URL
                    video_url = f"https://www.youtube.com/watch?v={video_id}"

                    # Parse upload date if available
                    pub_date = None
                    upload_date = entry.get("upload_date") or entry.get("timestamp")
                    if upload_date:
                        try:
                            if isinstance(upload_date, str):
                                pub_date = datetime.strptime(upload_date, "%Y%m%d")
                            elif isinstance(upload_date, (int, float)):
                                pub_date = datetime.fromtimestamp(upload_date)
                        except (ValueError, OSError):
                            pass

                    # Create episode object
                    episode = Episode(
                        title=entry.get("title", "Unknown Title"),
                        description=entry.get("description", "")[:500] if entry.get("description") else "",
                        audio_url=video_url,  # type: ignore[arg-type]  # video_url is str, Pydantic validates to HttpUrl
                        duration=str(entry.get("duration")) if entry.get("duration") else None,
                        guid=video_id,
                        pub_date=pub_date,
                    )
                    episodes.append(episode)

                logger.info(f"Returning {len(episodes)} valid episodes")
                return episodes

        except Exception as e:
            logger.error(f"Error getting episodes from YouTube: {e}", exc_info=True)
            return []

    def download_episode(self, episode: Episode, podcast_title: str) -> Optional[str]:
        """
        Download YouTube video as audio file and return local path.

        Args:
            episode: Episode object with audio_url pointing to YouTube video
            podcast_title: Title of the podcast (used for filename)

        Returns:
            Path to downloaded audio file or None if download fails
        """
        try:
            safe_podcast_title = self._sanitize_filename(podcast_title)
            safe_episode_title = self._sanitize_filename(episode.title)

            # Use video ID as hash
            video_id = episode.guid or hashlib.md5(str(episode.audio_url).encode()).hexdigest()[:8]

            filename = f"{safe_podcast_title}_{safe_episode_title}_{video_id}.m4a"
            local_path = self.storage_path / filename

            if local_path.exists():
                logger.info(f"File already exists: {filename}")
                return str(local_path)

            logger.info(f"Downloading from YouTube: {episode.title}")

            # yt-dlp options optimized for audio extraction
            ydl_opts = {
                "format": "bestaudio/best",  # Get best audio quality
                "outtmpl": str(local_path.with_suffix("")),  # Output template without extension
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "m4a",  # Convert to m4a
                    }
                ],
                "quiet": False,
                "no_warnings": False,
                "progress_hooks": [self._progress_hook],
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([str(episode.audio_url)])

            # yt-dlp might add .m4a extension automatically
            if not local_path.exists():
                # Check for the file without our explicit extension
                possible_path = self.storage_path / f"{safe_podcast_title}_{safe_episode_title}_{video_id}.m4a"
                if possible_path.exists():
                    local_path = possible_path
                else:
                    logger.warning("Download completed but file not found at expected location")
                    return None

            logger.info(f"Download completed: {filename}")
            return str(local_path)

        except Exception as e:
            logger.error(f"Error downloading YouTube video {episode.title}: {e}")
            return None

    def _progress_hook(self, d: Dict[str, Any]) -> None:
        """
        Progress callback for yt-dlp.

        Args:
            d: Dictionary with download progress information
        """
        if d["status"] == "downloading":
            if d.get("total_bytes"):
                progress = (d.get("downloaded_bytes", 0) / d["total_bytes"]) * 100
                # Use debug level to avoid cluttering logs with progress updates
                logger.debug(f"Progress: {progress:.1f}%")
        elif d["status"] == "finished":
            logger.info("Processing audio...")

    def _sanitize_filename(self, filename: str) -> str:
        """Remove/replace invalid filename characters"""
        # Replace special characters that are invalid in filenames
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, "_")

        # Replace spaces with underscores
        filename = filename.replace(" ", "_")

        # Replace dots with underscores to prevent yt-dlp from treating them as extensions
        # This ensures "World No.1" becomes "World_No_1" instead of being truncated
        filename = filename.replace(".", "_")

        # Replace other potentially problematic characters
        filename = filename.replace("&", "and")
        filename = filename.replace("!", "")
        filename = filename.replace("?", "")

        # Remove non-printable characters
        filename = "".join(c for c in filename if c.isprintable())

        # Remove multiple consecutive underscores
        while "__" in filename:
            filename = filename.replace("__", "_")

        # Trim to reasonable length (leaving room for video ID and extension)
        return filename[:100].strip("_")
