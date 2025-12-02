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

"""
Media Source Strategy Pattern for handling different podcast sources.

This module implements the Strategy Pattern to handle different types of media sources
(RSS feeds, YouTube channels/playlists, etc.) in a consistent way. Each source type
implements the MediaSource interface, providing URL validation, episode fetching,
and downloading capabilities.

Benefits:
- Separation of concerns: YouTube logic isolated from RSS logic
- Extensibility: Easy to add new sources (Spotify, SoundCloud, etc.)
- Testability: Each source can be tested independently
- Type safety: Explicit contracts via ABC
"""

import json
import logging
import re
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import feedparser
import requests

from ..models.podcast import Episode
from .youtube_downloader import YouTubeDownloader

if TYPE_CHECKING:
    from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)


class MediaSource(ABC):
    """
    Abstract base class for media sources.

    A media source represents a platform that provides podcast/audio content
    (e.g., RSS feeds, YouTube, Spotify). Each source must implement methods
    for URL validation, episode fetching, and downloading.
    """

    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is valid for this media source.

        Args:
            url: URL to validate

        Returns:
            True if URL matches this source's patterns, False otherwise
        """

    @abstractmethod
    def extract_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract metadata (title, description) from the source URL.

        Args:
            url: Source URL to extract metadata from

        Returns:
            Dictionary with 'title', 'description', and source-specific fields,
            or None if extraction fails
        """

    @abstractmethod
    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from the source.

        Args:
            url: Source URL to fetch from
            existing_episodes: List of episodes already tracked
            last_processed: Timestamp of last processed episode (for RSS incremental fetch)
            max_episodes: Optional limit on number of episodes to return

        Returns:
            List of new Episode objects discovered from the source
        """

    @abstractmethod
    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download episode audio file.

        Args:
            episode: Episode to download
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file

        Returns:
            Path to downloaded audio file, or None if download fails
        """


class RSSMediaSource(MediaSource):
    """
    Media source implementation for RSS/Atom podcast feeds.

    Handles:
    - Standard RSS podcast feeds
    - Apple Podcasts URLs (resolved to RSS via iTunes API)
    - Feedparser-based episode extraction
    - Saving raw RSS content for debugging (optional)
    """

    def __init__(self, path_manager: Optional["PathManager"] = None) -> None:
        """
        Initialize RSS media source.

        Args:
            path_manager: Optional path manager for saving debug RSS files.
                         If provided, raw RSS content will be saved during fetch_episodes.
        """
        self.path_manager = path_manager

    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is an RSS feed or Apple Podcasts URL.

        Args:
            url: URL to validate

        Returns:
            True if URL is RSS or Apple Podcasts, False otherwise
        """
        # Apple Podcasts URLs are handled by extracting RSS
        if "podcasts.apple.com" in url or "itunes.apple.com" in url:
            return True

        # Check for common RSS patterns
        rss_patterns = [r"\.xml$", r"\.rss$", r"/feed/?$", r"/rss/?$", r"/podcast/?$"]
        if any(re.search(pattern, url, re.IGNORECASE) for pattern in rss_patterns):
            return True

        # Default: Assume it's RSS if not YouTube or other known sources
        # This is a fallback since RSS feeds can have any URL structure
        return not YouTubeDownloader.is_youtube_url(url)

    def extract_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract podcast metadata from RSS feed or Apple Podcasts URL.

        Args:
            url: RSS feed URL or Apple Podcasts URL

        Returns:
            Dictionary with 'title', 'description', 'rss_url' or None if extraction fails
        """
        try:
            # Resolve Apple Podcasts URLs to RSS first
            rss_url = self._extract_rss_from_apple_url(url)
            if not rss_url:
                rss_url = url  # Assume it's already an RSS URL

            # Parse RSS feed
            parsed_feed = feedparser.parse(rss_url)
            if parsed_feed.bozo:
                logger.warning(f"Invalid RSS feed: {rss_url}")
                return None

            feed = parsed_feed.feed
            return {
                "title": feed.get("title", "Unknown Podcast"),
                "description": feed.get("description", ""),
                "rss_url": rss_url,
            }

        except Exception as e:
            logger.error(f"Error extracting RSS metadata from {url}: {e}")
            return None

    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
        podcast_slug: Optional[str] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from RSS feed.

        Args:
            url: RSS feed URL
            existing_episodes: List of episodes already tracked
            last_processed: Timestamp of last processed episode (for incremental fetch)
            max_episodes: Optional limit on number of episodes to return
            podcast_slug: Optional podcast slug for saving debug RSS file

        Returns:
            List of new Episode objects from the feed
        """
        try:
            # Fetch raw RSS content and optionally save for debugging
            rss_content = self._fetch_rss_content(url, podcast_slug)
            if rss_content is None:
                logger.warning(f"Failed to fetch RSS feed: {url}")
                return []

            # Parse the fetched content
            parsed_feed = feedparser.parse(rss_content)
            if parsed_feed.bozo:
                logger.warning(f"Invalid RSS feed during episode fetch: {url}")
                return []

            episodes = []
            for entry in parsed_feed.entries:
                episode_date = self._parse_date(entry.get("published_parsed"))
                episode_external_id = entry.get("guid", entry.get("id", str(episode_date)))

                # Skip episodes that already exist in the database
                # Check if episode already exists (regardless of state)
                existing_episode = next((ep for ep in existing_episodes if ep.external_id == episode_external_id), None)
                if existing_episode:
                    continue

                # Include episode if:
                # 1. last_processed is not set (first refresh), OR
                # 2. It's newer than last_processed, OR
                # 3. We have very few episodes tracked (indicates tracking was broken/reset)
                should_include = (
                    last_processed is None or episode_date > last_processed or len(existing_episodes) < 3
                )  # Assume most feeds have >3 episodes

                if should_include:
                    audio_url = self._extract_audio_url(entry)
                    if audio_url:
                        episode = Episode(
                            title=entry.get("title", "Unknown Episode"),
                            description=entry.get("description", ""),
                            pub_date=episode_date,
                            audio_url=audio_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
                            duration=entry.get("itunes_duration"),
                            external_id=episode_external_id,
                        )
                        episodes.append(episode)

            # Apply max_episodes limit if set
            if episodes and max_episodes:
                episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                episodes = episodes[:max_episodes]

            return episodes

        except Exception as e:
            logger.error(f"Error fetching episodes from RSS feed {url}: {e}")
            return []

    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download episode audio from RSS feed URL.

        Note: This method delegates to the calling code (AudioDownloader) which handles
        HTTP downloads with retry logic. RSS episodes are downloaded via standard HTTP.

        Args:
            episode: Episode to download
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file

        Returns:
            None (signals caller to use standard HTTP download)
        """
        # RSS episodes are downloaded via standard HTTP by AudioDownloader
        # This method returns None to signal that behavior
        return None

    def _fetch_rss_content(self, url: str, podcast_slug: Optional[str] = None) -> Optional[str]:
        """
        Fetch RSS content from URL and optionally save for debugging.

        Args:
            url: RSS feed URL
            podcast_slug: Optional podcast slug for saving debug file

        Returns:
            RSS content as string, or None if fetch fails
        """
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            rss_content = response.text

            # Save debug RSS file if path_manager and podcast_slug are provided
            if self.path_manager and podcast_slug:
                self._save_debug_rss(podcast_slug, rss_content)

            return rss_content

        except requests.RequestException as e:
            logger.error(f"Error fetching RSS feed {url}: {e}")
            return None

    def _save_debug_rss(self, podcast_slug: str, content: str) -> None:
        """
        Save RSS content to debug file for troubleshooting.

        Overwrites previous version on each refresh.

        Args:
            podcast_slug: Slugified podcast title
            content: Raw RSS XML content
        """
        try:
            if not self.path_manager:
                return

            debug_file = self.path_manager.debug_feed_file(podcast_slug)
            debug_file.parent.mkdir(parents=True, exist_ok=True)
            debug_file.write_text(content, encoding="utf-8")
            logger.debug(f"Saved debug RSS feed: {debug_file}")

        except Exception as e:
            # Don't fail the refresh if debug save fails
            logger.warning(f"Failed to save debug RSS feed for {podcast_slug}: {e}")

    def _extract_rss_from_apple_url(self, url: str) -> Optional[str]:
        """
        Extract RSS feed URL from Apple Podcast URL using iTunes Lookup API.

        Args:
            url: Apple Podcast URL

        Returns:
            RSS feed URL or None if not an Apple URL or extraction fails
        """
        try:
            # Check if this is an Apple Podcast URL
            if "podcasts.apple.com" not in url and "itunes.apple.com" not in url:
                return None

            # Extract podcast ID from URL
            id_match = re.search(r"id(\d+)", url)
            if not id_match:
                logger.warning(f"Could not extract podcast ID from Apple URL: {url}")
                return None

            podcast_id = id_match.group(1)

            # Use iTunes Lookup API to get RSS feed
            lookup_url = f"https://itunes.apple.com/lookup?id={podcast_id}"

            with urllib.request.urlopen(lookup_url) as response:
                data = json.load(response)

            if data.get("resultCount", 0) > 0:
                result = data["results"][0]
                feed_url = result.get("feedUrl")
                if feed_url:
                    logger.info(f"Extracted RSS feed from Apple Podcast: {feed_url}")
                    return str(feed_url)
                logger.warning(f"No RSS feed URL found for podcast ID {podcast_id}")
                return None

            # If the ID doesn't work, try to resolve redirect
            logger.info(f"No podcast found for ID {podcast_id}, attempting to resolve redirect...")
            return self._resolve_apple_podcast_redirect(url)

        except Exception as e:
            logger.error(f"Error extracting RSS from Apple URL {url}: {e}")
            return None

    def _resolve_apple_podcast_redirect(self, url: str) -> Optional[str]:
        """
        Resolve Apple Podcast redirects to get the actual podcast ID.

        Args:
            url: Apple Podcast URL that may redirect

        Returns:
            RSS feed URL if found, None otherwise
        """
        try:
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

            with urllib.request.urlopen(request) as response:
                page_content = response.read().decode("utf-8", errors="ignore")

                # Extract all potential IDs from the page content
                id_matches = re.findall(r"id(\d+)", page_content)

                # Try each ID found on the page
                for potential_id in set(id_matches):  # Use set to avoid duplicates
                    logger.debug(f"Trying podcast ID: {potential_id}")

                    lookup_url = f"https://itunes.apple.com/lookup?id={potential_id}"
                    try:
                        with urllib.request.urlopen(lookup_url) as api_response:
                            data = json.load(api_response)

                        if data.get("resultCount", 0) > 0:
                            result = data["results"][0]
                            feed_url = result.get("feedUrl")
                            if feed_url:
                                logger.info(f"Successfully found RSS feed with ID {potential_id}: {feed_url}")
                                return str(feed_url)
                    except Exception as id_error:
                        logger.debug(f"Failed to lookup ID {potential_id}: {id_error}")
                        continue

                return None

        except Exception as e:
            logger.error(f"Error resolving Apple Podcast redirect {url}: {e}")
            return None

    def _parse_date(self, date_tuple: Any) -> datetime:
        """
        Parse feedparser date tuple to datetime.

        Args:
            date_tuple: Feedparser date tuple (time.struct_time or None)

        Returns:
            Parsed datetime or current datetime if parsing fails
        """
        if date_tuple:
            try:
                return datetime(*date_tuple[:6])
            except (TypeError, ValueError):
                pass
        return datetime.now()

    def _extract_audio_url(self, entry: Any) -> Optional[str]:
        """
        Extract audio URL from feed entry.

        Args:
            entry: Feedparser entry object

        Returns:
            Audio URL if found, None otherwise
        """
        for link in entry.get("links", []):
            if link.get("type", "").startswith("audio/"):
                href = link.get("href")
                return str(href) if href else None

        for enclosure in entry.get("enclosures", []):
            if enclosure.get("type", "").startswith("audio/"):
                href = enclosure.get("href")
                return str(href) if href else None

        return None


class YouTubeMediaSource(MediaSource):
    """
    Media source implementation for YouTube channels and playlists.

    Handles:
    - YouTube videos, playlists, channels
    - yt-dlp-based episode extraction
    - Audio-only downloads
    """

    def __init__(self, storage_path: str):
        """
        Initialize YouTube media source.

        Args:
            storage_path: Directory for storing downloaded audio files
        """
        self.youtube_downloader = YouTubeDownloader(storage_path)

    def is_valid_url(self, url: str) -> bool:
        """
        Check if URL is a YouTube URL.

        Args:
            url: URL to validate

        Returns:
            True if URL is YouTube, False otherwise
        """
        return YouTubeDownloader.is_youtube_url(url)

    def extract_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Extract playlist/channel metadata from YouTube URL.

        Args:
            url: YouTube playlist or channel URL

        Returns:
            Dictionary with 'title', 'description', 'uploader' or None if extraction fails
        """
        try:
            playlist_info = self.youtube_downloader.extract_playlist_info(url)
            if not playlist_info:
                logger.warning(f"Could not extract YouTube playlist info from: {url}")
                return None

            return {
                "title": playlist_info.get("title", "Unknown YouTube Podcast"),
                "description": playlist_info.get("description", ""),
                "uploader": playlist_info.get("uploader", ""),
                "rss_url": url,  # YouTube URL is treated as RSS URL
            }

        except Exception as e:
            logger.error(f"Error extracting YouTube metadata from {url}: {e}")
            return None

    def fetch_episodes(
        self,
        url: str,
        existing_episodes: List[Episode],
        last_processed: Optional[datetime] = None,
        max_episodes: Optional[int] = None,
    ) -> List[Episode]:
        """
        Fetch new episodes from YouTube playlist/channel.

        Args:
            url: YouTube playlist or channel URL
            existing_episodes: List of episodes already tracked
            last_processed: Timestamp of last processed episode (unused for YouTube)
            max_episodes: Optional limit on number of episodes to return

        Returns:
            List of new Episode objects from YouTube
        """
        try:
            # Get all episodes from YouTube
            all_episodes = self.youtube_downloader.get_episodes_from_playlist(url)

            # Apply limit before filtering (most recent episodes first)
            if max_episodes:
                all_episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                all_episodes = all_episodes[:max_episodes]

            # Filter out episodes that already exist in the database
            new_episodes = []
            for episode in all_episodes:
                # Check if episode already exists (regardless of state)
                existing_episode = next((ep for ep in existing_episodes if ep.external_id == episode.external_id), None)
                if not existing_episode:
                    new_episodes.append(episode)

            return new_episodes

        except Exception as e:
            logger.error(f"Error fetching episodes from YouTube {url}: {e}")
            return []

    def download_episode(self, episode: Episode, podcast_title: str, storage_path: str) -> Optional[str]:
        """
        Download YouTube video as audio file.

        Args:
            episode: Episode to download (with YouTube URL)
            podcast_title: Podcast title (for filename generation)
            storage_path: Directory to save audio file (unused, YouTubeDownloader has its own)

        Returns:
            Path to downloaded audio file, or None if download fails
        """
        return self.youtube_downloader.download_episode(episode, podcast_title)


class MediaSourceFactory:
    """
    Factory for detecting and creating appropriate media source instances.

    Automatically detects the source type from URL patterns and returns
    the appropriate MediaSource implementation.
    """

    def __init__(self, storage_path: str, path_manager: Optional["PathManager"] = None):
        """
        Initialize media source factory.

        Args:
            storage_path: Directory for storing downloaded audio files
            path_manager: Optional path manager for debug RSS saving
        """
        self.storage_path = storage_path
        self.path_manager = path_manager
        # Initialize sources
        self._youtube_source = YouTubeMediaSource(storage_path)
        self._rss_source = RSSMediaSource(path_manager)

    def detect_source(self, url: str) -> MediaSource:
        """
        Detect media source type from URL and return appropriate implementation.

        Detection order:
        1. YouTube (explicit patterns)
        2. RSS (default fallback)

        Args:
            url: URL to detect source type for

        Returns:
            MediaSource implementation for the detected source type
        """
        # Check YouTube first (explicit patterns)
        if self._youtube_source.is_valid_url(url):
            return self._youtube_source

        # Default to RSS (handles RSS feeds and Apple Podcasts)
        return self._rss_source

    def get_youtube_source(self) -> YouTubeMediaSource:
        """
        Get YouTube source instance.

        Returns:
            YouTubeMediaSource instance
        """
        return self._youtube_source

    def get_rss_source(self) -> RSSMediaSource:
        """
        Get RSS source instance.

        Returns:
            RSSMediaSource instance
        """
        return self._rss_source
