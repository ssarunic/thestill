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

import json
import logging
import re
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import feedparser

from ..models.podcast import Episode, Podcast
from ..repositories.podcast_repository import PodcastRepository
from ..utils.path_manager import PathManager
from .youtube_downloader import YouTubeDownloader

logger = logging.getLogger(__name__)


class PodcastFeedManager:
    """
    Manages podcast feeds and episodes.

    Responsibilities:
    - Fetch RSS/YouTube feeds
    - Parse feed data
    - Coordinate episode discovery
    - Manage episode state transitions

    Does NOT handle:
    - Data persistence (delegates to repository)
    - Business logic (delegates to service layer)
    """

    def __init__(self, podcast_repository: PodcastRepository, path_manager: PathManager) -> None:
        """
        Initialize feed manager.

        Args:
            podcast_repository: Repository for persistence
            path_manager: Path manager for file operations
        """
        self.repository: PodcastRepository = podcast_repository
        self.path_manager: PathManager = path_manager
        self.storage_path: Path = Path(path_manager.storage_path)
        self.storage_path.mkdir(exist_ok=True)
        self.youtube_downloader: YouTubeDownloader = YouTubeDownloader(str(self.path_manager.original_audio_dir()))
        self._in_transaction: bool = False
        self._transaction_podcasts: Dict[str, Podcast] = {}

    @contextmanager
    def transaction(self):
        """
        Context manager for batch updates with deferred save.

        Use this when performing multiple episode state updates to avoid
        multiple file I/O operations. The repository save will happen
        once when the context exits.

        Example:
            with feed_manager.transaction():
                feed_manager.mark_episode_downloaded(url1, guid1, path1)
                feed_manager.mark_episode_downsampled(url1, guid1, path2)
                feed_manager.mark_episode_processed(url1, guid1, raw_path, clean_path)
            # Auto-saves once at end

        Note:
            - Nested transactions are not supported (inner transaction is no-op)
            - All updates within transaction apply to in-memory podcast objects
            - Changes are persisted to disk only when context exits normally
            - If an exception occurs, changes may still be persisted (no rollback)
        """
        # If already in transaction, this is a no-op (nested transaction)
        if self._in_transaction:
            yield self
            return

        # Start transaction
        self._in_transaction = True
        self._transaction_podcasts = {}

        try:
            yield self
        finally:
            # Commit: Save all modified podcasts
            for podcast in self._transaction_podcasts.values():
                self.repository.save(podcast)

            # Clear transaction state
            self._in_transaction = False
            self._transaction_podcasts = {}

    def _get_or_cache_podcast(self, podcast_rss_url: str) -> Optional[Podcast]:
        """
        Get podcast from transaction cache or repository.

        Helper for transaction-aware episode updates. Loads podcast from
        repository on first access within transaction and caches for subsequent updates.

        Args:
            podcast_rss_url: RSS URL of the podcast

        Returns:
            Podcast object if found, None otherwise
        """
        # Check cache first
        if podcast_rss_url in self._transaction_podcasts:
            return self._transaction_podcasts[podcast_rss_url]

        # Load from repository and cache
        podcast = self.repository.find_by_url(podcast_rss_url)
        if podcast:
            self._transaction_podcasts[podcast_rss_url] = podcast
        return podcast

    def add_podcast(self, url: str) -> bool:
        """Add a new podcast feed - handles RSS URLs, Apple Podcast URLs, and YouTube URLs"""
        try:
            # Check if this is a YouTube URL
            if self.youtube_downloader.is_youtube_url(url):
                return self._add_youtube_podcast(url)

            # Check if this is an Apple Podcast URL and extract RSS if needed
            rss_url = self._extract_rss_from_apple_url(url)
            if not rss_url:
                rss_url = url  # Assume it's already an RSS URL

            parsed_feed = feedparser.parse(rss_url)
            if parsed_feed.bozo:
                raise ValueError(f"Invalid RSS feed: {rss_url}")

            feed = parsed_feed.feed
            podcast = Podcast(
                title=feed.get("title", "Unknown Podcast"),
                description=feed.get("description", ""),
                rss_url=rss_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
            )

            if not self.repository.exists(rss_url):
                self.repository.save(podcast)
                return True
            return False

        except Exception as e:
            logger.error(f"Error adding podcast {url}: {e}")
            return False

    def remove_podcast(self, rss_url: str) -> bool:
        """Remove a podcast feed"""
        return self.repository.delete(rss_url)

    def get_new_episodes(self, max_episodes_per_podcast: Optional[int] = None) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Check all feeds for new episodes.

        Args:
            max_episodes_per_podcast: Optional limit on episodes to discover per podcast.
                                     If set, only the N most recent episodes will be tracked.

        Returns:
            List of tuples containing (Podcast, List[Episode]) for podcasts with new episodes
        """
        new_episodes = []
        podcasts = self.repository.find_all()

        for podcast in podcasts:
            try:
                # Check if this is a YouTube podcast
                if self.youtube_downloader.is_youtube_url(str(podcast.rss_url)):
                    episodes = self._get_youtube_episodes(podcast, max_episodes_per_podcast)
                    if episodes:
                        new_episodes.append((podcast, episodes))
                    continue

                # Handle regular RSS feeds
                parsed_feed = feedparser.parse(str(podcast.rss_url))
                episodes = []

                for entry in parsed_feed.entries:
                    episode_date = self._parse_date(entry.get("published_parsed"))
                    episode_guid = entry.get("guid", entry.get("id", str(episode_date)))

                    # Check if this episode is already processed
                    already_processed = any(ep.guid == episode_guid and ep.processed for ep in podcast.episodes)
                    if already_processed:
                        continue

                    # Include episode if:
                    # 1. It's newer than last_processed, OR
                    # 2. We have very few processed episodes (indicates tracking was broken)
                    num_processed_episodes = len([ep for ep in podcast.episodes if ep.processed])

                    should_include = (
                        podcast.last_processed is None
                        or episode_date > podcast.last_processed
                        or num_processed_episodes < 3
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
                                guid=episode_guid,
                            )

                            # Check if episode already exists in podcast.episodes (but not processed)
                            existing_episode = next((ep for ep in podcast.episodes if ep.guid == episode_guid), None)
                            if not existing_episode:
                                podcast.episodes.append(episode)

                            episodes.append(episode)

                # Apply max_episodes_per_podcast limit if set
                if episodes and max_episodes_per_podcast:
                    # Sort by pub_date (most recent first) and apply limit
                    episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                    episodes = episodes[:max_episodes_per_podcast]

                    # Also trim podcast.episodes to respect the limit
                    # Keep already processed episodes + most recent unprocessed episodes up to limit
                    processed_eps = [ep for ep in podcast.episodes if ep.processed]
                    unprocessed_eps = [ep for ep in podcast.episodes if not ep.processed]
                    unprocessed_eps.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)

                    # Calculate available slots for unprocessed episodes
                    total_limit = max_episodes_per_podcast
                    available_slots = max(0, total_limit - len(processed_eps))
                    podcast.episodes = processed_eps + unprocessed_eps[:available_slots]

                if episodes:
                    new_episodes.append((podcast, episodes))

                # Save podcast with new episodes
                self.repository.save(podcast)

            except Exception as e:
                logger.error(f"Error checking feed {podcast.rss_url}: {e}")
                continue

        return new_episodes

    def mark_episode_downloaded(self, podcast_rss_url: str, episode_guid: str, audio_path: str) -> None:
        """
        Mark an episode as downloaded with audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_guid: GUID of the episode
            audio_path: Path to the downloaded audio file
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.guid == episode_guid:
                        episode.audio_path = audio_path
                        logger.info(f"Marked episode as downloaded (in transaction): {episode_guid}")
                        return
                logger.warning(f"Episode not found for download marking: {episode_guid}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            success = self.repository.update_episode(podcast_rss_url, episode_guid, {"audio_path": audio_path})
            if success:
                logger.info(f"Marked episode as downloaded: {episode_guid}")
            else:
                logger.warning(f"Episode not found for download marking: {episode_guid}")

    def mark_episode_downsampled(self, podcast_rss_url: str, episode_guid: str, downsampled_audio_path: str) -> None:
        """
        Mark an episode as downsampled with downsampled audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_guid: GUID of the episode
            downsampled_audio_path: Path to the downsampled audio file
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.guid == episode_guid:
                        episode.downsampled_audio_path = downsampled_audio_path
                        logger.info(f"Marked episode as downsampled (in transaction): {episode_guid}")
                        return
                logger.warning(f"Episode not found for downsample marking: {episode_guid}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            success = self.repository.update_episode(
                podcast_rss_url, episode_guid, {"downsampled_audio_path": downsampled_audio_path}
            )
            if success:
                logger.info(f"Marked episode as downsampled: {episode_guid}")
            else:
                logger.warning(f"Episode not found for downsample marking: {episode_guid}")

    def mark_episode_processed(
        self,
        podcast_rss_url: str,
        episode_guid: str,
        raw_transcript_path: Optional[str] = None,
        clean_transcript_path: Optional[str] = None,
        summary_path: Optional[str] = None,
    ) -> None:
        """
        Mark an episode as processed.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_guid: GUID of the episode
            raw_transcript_path: Optional path to raw transcript file
            clean_transcript_path: Optional path to cleaned transcript file
            summary_path: Optional path to summary file
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                episode_found = False
                for episode in podcast.episodes:
                    if episode.guid == episode_guid:
                        episode.processed = True
                        if raw_transcript_path:
                            episode.raw_transcript_path = raw_transcript_path
                        if clean_transcript_path:
                            episode.clean_transcript_path = clean_transcript_path
                        if summary_path:
                            episode.summary_path = summary_path
                        podcast.last_processed = datetime.now()
                        logger.info(f"Marked episode as processed (in transaction): {episode_guid}")
                        episode_found = True
                        break

                if not episode_found:
                    logger.warning(f"Episode not found for processing marking: {episode_guid}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update (original logic)
            # Build updates dictionary
            updates: Dict[str, Any] = {"processed": True}
            if raw_transcript_path:
                updates["raw_transcript_path"] = raw_transcript_path
            if clean_transcript_path:
                updates["clean_transcript_path"] = clean_transcript_path
            if summary_path:
                updates["summary_path"] = summary_path

            # Try to update existing episode
            success = self.repository.update_episode(podcast_rss_url, episode_guid, updates)

            # If episode not found in stored episodes, fetch it from RSS and add it
            if not success:
                try:
                    podcast = self.repository.find_by_url(podcast_rss_url)
                    if not podcast:
                        logger.error(f"Podcast not found: {podcast_rss_url}")
                        return

                    parsed_feed = feedparser.parse(str(podcast.rss_url))
                    for entry in parsed_feed.entries:
                        entry_guid = entry.get("guid", entry.get("id", ""))
                        if entry_guid == episode_guid:
                            episode_date = self._parse_date(entry.get("published_parsed"))
                            audio_url = self._extract_audio_url(entry)
                            if audio_url:
                                episode = Episode(
                                    title=entry.get("title", "Unknown Episode"),
                                    description=entry.get("description", ""),
                                    pub_date=episode_date,
                                    audio_url=audio_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
                                    duration=entry.get("itunes_duration"),
                                    guid=entry_guid,
                                    processed=True,
                                    raw_transcript_path=raw_transcript_path,
                                    clean_transcript_path=clean_transcript_path,
                                    summary_path=summary_path,
                                )
                                podcast.episodes.append(episode)
                                podcast.last_processed = datetime.now()
                                self.repository.save(podcast)
                                logger.info(f"Added and marked new episode as processed: {episode.title}")
                                return
                except Exception as e:
                    logger.error(f"Error fetching episode info for {episode_guid}: {e}")
                    return

            # Update podcast last_processed timestamp
            podcast = self.repository.find_by_url(podcast_rss_url)
            if podcast:
                podcast.last_processed = datetime.now()
                self.repository.save(podcast)
                logger.info(f"Marked episode as processed: {episode_guid}")

    def get_downloaded_episodes(self, storage_path: str) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Get all episodes that have downsampled audio but need transcription.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of tuples containing (Podcast, List[Episode]) for episodes needing transcription
        """
        episodes_to_transcribe = []
        podcasts = self.repository.find_all()

        for podcast in podcasts:
            episodes = []
            for episode in podcast.episodes:
                # Check if downsampled audio exists (required for transcription)
                if not episode.downsampled_audio_path:
                    continue

                # Check if downsampled audio file actually exists
                if not self.path_manager.downsampled_audio_file(episode.downsampled_audio_path).exists():
                    continue

                # Check if transcript doesn't exist or file is missing
                needs_transcription = False
                if not episode.raw_transcript_path:
                    needs_transcription = True
                else:
                    if not self.path_manager.raw_transcript_file(episode.raw_transcript_path).exists():
                        needs_transcription = True

                if needs_transcription:
                    episodes.append(episode)

            if episodes:
                episodes_to_transcribe.append((podcast, episodes))

        return episodes_to_transcribe

    def get_episodes_to_download(self, storage_path: str) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Get all episodes that need audio download (have audio_url but no audio_path).

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of tuples containing (Podcast, List[Episode]) for episodes needing download
        """
        episodes_to_download = []
        podcasts = self.repository.find_all()

        for podcast in podcasts:
            episodes = []
            for episode in podcast.episodes:
                # Check if episode has audio URL
                if not episode.audio_url:
                    continue

                # Check if audio is not yet downloaded or file is missing
                needs_download = False
                if not episode.audio_path:
                    needs_download = True
                else:
                    if not self.path_manager.original_audio_file(episode.audio_path).exists():
                        needs_download = True

                if needs_download:
                    episodes.append(episode)

            if episodes:
                episodes_to_download.append((podcast, episodes))

        return episodes_to_download

    def get_episodes_to_downsample(self, storage_path: str) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Get all episodes that have downloaded audio but need downsampling.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of tuples containing (Podcast, List[Episode]) for episodes needing downsampling
        """
        episodes_to_downsample = []
        podcasts = self.repository.find_all()

        for podcast in podcasts:
            episodes = []
            for episode in podcast.episodes:
                # Check if original audio is downloaded
                if not episode.audio_path:
                    continue

                # Check if original audio file actually exists
                if not self.path_manager.original_audio_file(episode.audio_path).exists():
                    continue

                # Check if downsampled version doesn't exist or file is missing
                needs_downsampling = False
                if not episode.downsampled_audio_path:
                    needs_downsampling = True
                else:
                    if not self.path_manager.downsampled_audio_file(episode.downsampled_audio_path).exists():
                        needs_downsampling = True

                if needs_downsampling:
                    episodes.append(episode)

            if episodes:
                episodes_to_downsample.append((podcast, episodes))

        return episodes_to_downsample

    def list_podcasts(self) -> List[Podcast]:
        """Return list of all podcasts"""
        return self.repository.find_all()

    def _extract_rss_from_apple_url(self, url: str) -> Optional[str]:
        """Extract RSS feed URL from Apple Podcast URL using iTunes Lookup API"""
        try:
            # Check if this is an Apple Podcast URL
            if "podcasts.apple.com" not in url and "itunes.apple.com" not in url:
                return None

            # Extract podcast ID from URL
            # URLs can be like:
            # https://podcasts.apple.com/gb/channel/the-rest-is-politics/id6443145599
            # https://itunes.apple.com/us/podcast/podcast-name/id1234567890
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
                    return str(feed_url)  # Ensure we return str
                logger.warning(f"No RSS feed URL found for podcast ID {podcast_id}")
                return None
            # If the ID doesn't work, try to get the page and extract the real ID
            logger.info(f"No podcast found for ID {podcast_id}, attempting to resolve redirect...")
            return self._resolve_apple_podcast_redirect(url)

        except Exception as e:
            logger.error(f"Error extracting RSS from Apple URL {url}: {e}")
            return None

    def _get_youtube_episodes(self, podcast: Podcast, max_episodes_per_podcast: Optional[int] = None) -> List[Episode]:
        """
        Get new episodes from a YouTube playlist/channel.

        Args:
            podcast: The podcast to get episodes for
            max_episodes_per_podcast: Optional limit on episodes to discover

        Returns:
            List of new episodes from YouTube
        """
        try:
            # Get all episodes from YouTube
            all_episodes = self.youtube_downloader.get_episodes_from_playlist(str(podcast.rss_url))

            # Apply limit before filtering (most recent episodes first)
            if max_episodes_per_podcast:
                all_episodes.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)
                all_episodes = all_episodes[:max_episodes_per_podcast]

            # Filter out already processed episodes
            new_episodes = []
            for episode in all_episodes:
                already_processed = any(ep.guid == episode.guid and ep.processed for ep in podcast.episodes)
                if not already_processed:
                    # Check if episode already exists in podcast.episodes (but not processed)
                    existing_episode = next((ep for ep in podcast.episodes if ep.guid == episode.guid), None)
                    if not existing_episode:
                        podcast.episodes.append(episode)
                    new_episodes.append(episode)

            # Apply limit to podcast.episodes as well (similar to RSS logic)
            if max_episodes_per_podcast:
                processed_eps = [ep for ep in podcast.episodes if ep.processed]
                unprocessed_eps = [ep for ep in podcast.episodes if not ep.processed]
                unprocessed_eps.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)

                total_limit = max_episodes_per_podcast
                available_slots = max(0, total_limit - len(processed_eps))
                podcast.episodes = processed_eps + unprocessed_eps[:available_slots]

            return new_episodes

        except Exception as e:
            logger.error(f"Error getting YouTube episodes for {podcast.rss_url}: {e}")
            return []

    def _add_youtube_podcast(self, url: str) -> bool:
        """Add a YouTube playlist/channel as a podcast"""
        try:
            playlist_info = self.youtube_downloader.extract_playlist_info(url)
            if not playlist_info:
                logger.warning(f"Could not extract YouTube playlist info from: {url}")
                return False

            # Create podcast entry with YouTube URL
            podcast = Podcast(
                title=playlist_info.get("title", "Unknown YouTube Podcast"),
                description=playlist_info.get("description", ""),
                rss_url=url,  # type: ignore[arg-type]  # YouTube URL treated as RSS URL, Pydantic validates
            )

            if not self.repository.exists(url):
                self.repository.save(podcast)
                logger.info(f"Added YouTube podcast: {podcast.title}")
                return True
            return False

        except Exception as e:
            logger.error(f"Error adding YouTube podcast {url}: {e}")
            return False

    def _resolve_apple_podcast_redirect(self, url: str) -> Optional[str]:
        """
        Resolve Apple Podcast redirects to get the actual podcast ID.

        Args:
            url: Apple Podcast URL that may redirect

        Returns:
            RSS feed URL if found, None otherwise
        """
        try:
            # Some Apple Podcast URLs redirect to different IDs
            # We'll make a request and follow redirects to get the real URL
            request = urllib.request.Request(url)
            request.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36")

            with urllib.request.urlopen(request) as response:
                _ = response.geturl()
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
                                return str(feed_url)  # Ensure we return str
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
