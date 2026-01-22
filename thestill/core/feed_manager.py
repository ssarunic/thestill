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

import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import feedparser

from ..models.podcast import Episode, Podcast
from ..repositories.podcast_repository import PodcastRepository
from ..utils.duration import parse_duration
from ..utils.path_manager import PathManager
from .media_source import MediaSourceFactory, RSSMediaSource

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
        self.media_source_factory: MediaSourceFactory = MediaSourceFactory(
            str(self.path_manager.original_audio_dir()),
            path_manager=path_manager,
        )
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
                feed_manager.mark_episode_downloaded(url1, external_id1, path1)
                feed_manager.mark_episode_downsampled(url1, external_id1, path2)
                feed_manager.mark_episode_processed(url1, external_id1, raw_path, clean_path)
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
        podcast = self.repository.get_by_url(podcast_rss_url)
        if podcast:
            self._transaction_podcasts[podcast_rss_url] = podcast
        return podcast

    def add_podcast(self, url: str) -> Optional[Podcast]:
        """
        Add a new podcast feed - handles RSS URLs, Apple Podcast URLs, and YouTube URLs.

        Returns:
            The newly added Podcast object, or None if failed or already exists.
        """
        try:
            # Detect source type and extract metadata
            source = self.media_source_factory.detect_source(url)
            metadata = source.extract_metadata(url)

            if not metadata:
                logger.error(f"Could not extract metadata from {url}")
                return None

            # Create podcast entry
            podcast = Podcast(
                title=metadata.get("title", "Unknown Podcast"),
                description=metadata.get("description", ""),
                rss_url=metadata.get("rss_url", url),  # type: ignore[arg-type]  # Pydantic validates to HttpUrl
                image_url=metadata.get("image_url"),
                language=metadata.get("language", "en"),
            )

            # Save if not already exists, otherwise return existing
            podcast_url = str(podcast.rss_url)
            if not self.repository.exists(podcast_url):
                self.repository.save(podcast)
                logger.info(f"Added podcast: {podcast.title}")
                return podcast
            # Return existing podcast (idempotent behavior)
            existing = self.repository.get_by_url(podcast_url)
            logger.info(f"Podcast already exists: {existing.title if existing else podcast_url}")
            return existing

        except Exception as e:
            logger.error(f"Error adding podcast {url}: {e}")
            return None

    def remove_podcast(self, rss_url: str) -> bool:
        """Remove a podcast feed"""
        return self.repository.delete(rss_url)

    def get_new_episodes(
        self,
        max_episodes_per_podcast: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        podcast_id: Optional[str] = None,
    ) -> List[Tuple[Podcast, List[Episode]]]:
        """
        Check feeds for new episodes.

        Args:
            max_episodes_per_podcast: Optional limit on episodes to discover per podcast.
                                     If set, only the N most recent episodes will be tracked.
            progress_callback: Optional callback for progress reporting.
                              Called with (current_index, total_count, podcast_title).
            podcast_id: Optional podcast ID or RSS URL to filter. If provided, only
                       this podcast's feed will be checked.

        Returns:
            List of tuples containing (Podcast, List[Episode]) for podcasts with new episodes
        """
        new_episodes = []

        # Get podcasts - filter to single podcast if podcast_id is provided
        if podcast_id:
            # Try to find by RSS URL first, then by ID
            podcast = self.repository.get_by_url(podcast_id)
            if not podcast:
                podcast = self.repository.get_by_id(podcast_id)
            if not podcast:
                logger.warning(f"Podcast not found for refresh: {podcast_id}")
                return []
            podcasts = [podcast]
        else:
            podcasts = self.repository.get_all()
        total_podcasts = len(podcasts)

        for idx, podcast in enumerate(podcasts):
            # Report progress
            if progress_callback:
                progress_callback(idx, total_podcasts, podcast.title)
            try:
                # Detect source type and fetch episodes
                source = self.media_source_factory.detect_source(str(podcast.rss_url))

                # Build fetch arguments - include podcast_slug for RSS sources (debug RSS saving)
                fetch_kwargs: Dict[str, Any] = {
                    "url": str(podcast.rss_url),
                    "existing_episodes": podcast.episodes,
                    "last_processed": podcast.last_processed,
                    "max_episodes": max_episodes_per_podcast,
                }

                # Add podcast_slug for RSS sources to enable debug RSS saving
                language_changed = False
                if isinstance(source, RSSMediaSource):
                    fetch_kwargs["podcast_slug"] = podcast.slug

                    # Update language from RSS feed if it changed (or was never set)
                    metadata = source.extract_metadata(str(podcast.rss_url))
                    if metadata and metadata.get("language"):
                        new_language = metadata["language"]
                        if podcast.language != new_language:
                            logger.info(f"Updating podcast language: {podcast.language} -> {new_language}")
                            podcast.language = new_language
                            language_changed = True

                episodes = source.fetch_episodes(**fetch_kwargs)

                # Save podcast if language changed (even if no new episodes)
                if language_changed and not episodes:
                    self.repository.save_podcast(podcast)

                # Add new episodes to podcast
                for episode in episodes:
                    existing_episode = next(
                        (ep for ep in podcast.episodes if ep.external_id == episode.external_id), None
                    )
                    if not existing_episode:
                        podcast.episodes.append(episode)

                # Apply max_episodes_per_podcast limit if set
                if episodes and max_episodes_per_podcast:
                    # Keep already processed episodes + most recent unprocessed episodes up to limit
                    from ..models.podcast import EpisodeState

                    processed_eps = [ep for ep in podcast.episodes if ep.state == EpisodeState.CLEANED]
                    unprocessed_eps = [ep for ep in podcast.episodes if ep.state != EpisodeState.CLEANED]
                    unprocessed_eps.sort(key=lambda e: e.pub_date or datetime.min, reverse=True)

                    # Calculate available slots for unprocessed episodes
                    total_limit = max_episodes_per_podcast
                    available_slots = max(0, total_limit - len(processed_eps))
                    podcast.episodes = processed_eps + unprocessed_eps[:available_slots]

                if episodes:
                    new_episodes.append((podcast, episodes))

                    # Update last_processed to the most recent episode's pub_date
                    # This ensures next refresh only considers episodes newer than what we've seen
                    if podcast.episodes:
                        most_recent_date = max((ep.pub_date for ep in podcast.episodes if ep.pub_date), default=None)
                        if most_recent_date:
                            podcast.last_processed = most_recent_date

                    # Save new episodes and update podcast metadata
                    # Use targeted saves to avoid updating unchanged episode timestamps
                    for episode in episodes:
                        episode.podcast_id = podcast.id
                    self.repository.save_episodes(episodes)
                    self.repository.save_podcast(podcast)

                    # Extract and save transcript links for RSS sources (Podcasting 2.0)
                    if isinstance(source, RSSMediaSource):
                        self._save_transcript_links_for_episodes(podcast, episodes, source)

            except Exception as e:
                logger.error(f"Error checking feed {podcast.rss_url}: {e}")
                continue

        return new_episodes

    def _save_transcript_links_for_episodes(
        self,
        podcast: Podcast,
        episodes: List[Episode],
        source: RSSMediaSource,
    ) -> None:
        """
        Extract and save transcript links for newly discovered episodes.

        Reads the debug RSS file (saved during fetch_episodes) and extracts
        <podcast:transcript> tags for each episode. Saves links to database
        for later download.

        Args:
            podcast: The podcast these episodes belong to
            episodes: List of newly discovered episodes
            source: The RSSMediaSource that fetched the episodes
        """
        try:
            # Read the debug RSS file (saved during fetch_episodes)
            debug_file = self.path_manager.debug_feed_file(podcast.slug)
            if not debug_file.exists():
                logger.debug(f"No debug RSS file found for {podcast.slug}, skipping transcript link extraction")
                return

            rss_content = debug_file.read_text(encoding="utf-8")

            # Extract transcript links from RSS
            transcript_links_by_guid = source.extract_transcript_links(rss_content)
            if not transcript_links_by_guid:
                return

            # Save transcript links for each new episode
            total_saved = 0
            for episode in episodes:
                links = transcript_links_by_guid.get(episode.external_id, [])
                if links:
                    # Use repository method to save links
                    saved = self.repository.add_transcript_links(episode.id, links)
                    if saved > 0:
                        total_saved += saved
                        logger.debug(f"Saved {saved} transcript links for episode {episode.title[:50]}...")

            if total_saved > 0:
                logger.info(f"Saved {total_saved} transcript links for {len(episodes)} new episodes in {podcast.title}")

        except Exception as e:
            # Don't fail episode discovery if transcript link extraction fails
            logger.warning(f"Failed to extract transcript links for {podcast.title}: {e}")

    def mark_episode_downloaded(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        audio_path: str,
        duration: Optional[int] = None,
    ) -> None:
        """
        Mark an episode as downloaded with audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
            audio_path: Path to the downloaded audio file
            duration: Optional duration in seconds from ffprobe
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.audio_path = audio_path
                        if duration is not None:
                            episode.duration = duration
                        logger.info(f"Marked episode as downloaded (in transaction): {episode_external_id}")
                        return
                logger.warning(f"Episode not found for download marking: {episode_external_id}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            updates = {"audio_path": audio_path}
            if duration is not None:
                updates["duration"] = duration
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)
            if success:
                logger.info(f"Marked episode as downloaded: {episode_external_id}")
            else:
                logger.warning(f"Episode not found for download marking: {episode_external_id}")

    def mark_episode_downsampled(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        downsampled_audio_path: str,
        duration: Optional[int] = None,
    ) -> None:
        """
        Mark an episode as downsampled with downsampled audio file path.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
            downsampled_audio_path: Path to the downsampled audio file
            duration: Optional duration in seconds from ffprobe
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.downsampled_audio_path = downsampled_audio_path
                        if duration is not None:
                            episode.duration = duration
                        logger.info(f"Marked episode as downsampled (in transaction): {episode_external_id}")
                        return
                logger.warning(f"Episode not found for downsample marking: {episode_external_id}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            updates = {"downsampled_audio_path": downsampled_audio_path}
            if duration is not None:
                updates["duration"] = duration
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)
            if success:
                logger.info(f"Marked episode as downsampled: {episode_external_id}")
            else:
                logger.warning(f"Episode not found for downsample marking: {episode_external_id}")

    def clear_episode_audio_path(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
    ) -> None:
        """
        Clear the audio_path field for an episode after the original audio file has been deleted.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.audio_path = None
                        logger.info(f"Cleared audio_path (in transaction): {episode_external_id}")
                        return
                logger.warning(f"Episode not found for clearing audio_path: {episode_external_id}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, {"audio_path": None})
            if success:
                logger.info(f"Cleared audio_path: {episode_external_id}")
            else:
                logger.warning(f"Episode not found for clearing audio_path: {episode_external_id}")

    def clear_episode_downsampled_audio_path(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
    ) -> None:
        """
        Clear the downsampled_audio_path field for an episode after the downsampled audio file has been deleted.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
        """
        if self._in_transaction:
            # Update in-memory cache
            podcast = self._get_or_cache_podcast(podcast_rss_url)
            if podcast:
                for episode in podcast.episodes:
                    if episode.external_id == episode_external_id:
                        episode.downsampled_audio_path = None
                        logger.info(f"Cleared downsampled_audio_path (in transaction): {episode_external_id}")
                        return
                logger.warning(f"Episode not found for clearing downsampled_audio_path: {episode_external_id}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update
            success = self.repository.update_episode(
                podcast_rss_url, episode_external_id, {"downsampled_audio_path": None}
            )
            if success:
                logger.info(f"Cleared downsampled_audio_path: {episode_external_id}")
            else:
                logger.warning(f"Episode not found for clearing downsampled_audio_path: {episode_external_id}")

    def mark_episode_processed(
        self,
        podcast_rss_url: str,
        episode_external_id: str,
        raw_transcript_path: Optional[str] = None,
        clean_transcript_path: Optional[str] = None,
        summary_path: Optional[str] = None,
    ) -> None:
        """
        Mark an episode as processed.

        Args:
            podcast_rss_url: RSS URL of the podcast
            episode_external_id: External ID (from RSS feed) of the episode
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
                    if episode.external_id == episode_external_id:
                        # Set file paths - state will be auto-computed by model validator
                        # None = don't update, "" = clear the field, "path" = set the field
                        if raw_transcript_path is not None:
                            episode.raw_transcript_path = raw_transcript_path if raw_transcript_path else None
                        if clean_transcript_path is not None:
                            episode.clean_transcript_path = clean_transcript_path if clean_transcript_path else None
                        if summary_path is not None:
                            episode.summary_path = summary_path if summary_path else None
                        podcast.last_processed = datetime.now()
                        logger.info(f"Marked episode as processed (in transaction): {episode_external_id}")
                        episode_found = True
                        break

                if not episode_found:
                    logger.warning(f"Episode not found for processing marking: {episode_external_id}")
            else:
                logger.warning(f"Podcast not found: {podcast_rss_url}")
        else:
            # Direct repository update (original logic)
            # Build updates dictionary - only file paths, state will be auto-computed
            # None = don't update, "" = clear the field, "path" = set the field
            updates: Dict[str, Any] = {}
            if raw_transcript_path is not None:
                updates["raw_transcript_path"] = raw_transcript_path if raw_transcript_path else None
            if clean_transcript_path is not None:
                updates["clean_transcript_path"] = clean_transcript_path if clean_transcript_path else None
            if summary_path is not None:
                updates["summary_path"] = summary_path if summary_path else None

            # Try to update existing episode
            success = self.repository.update_episode(podcast_rss_url, episode_external_id, updates)

            # If episode not found in stored episodes, fetch it from RSS and add it
            if not success:
                try:
                    podcast = self.repository.get_by_url(podcast_rss_url)
                    if not podcast:
                        logger.error(f"Podcast not found: {podcast_rss_url}")
                        return

                    parsed_feed = feedparser.parse(str(podcast.rss_url))
                    for entry in parsed_feed.entries:
                        entry_external_id = entry.get("guid", entry.get("id", ""))
                        if entry_external_id == episode_external_id:
                            episode_date = self._parse_date(entry.get("published_parsed"))
                            audio_url = self._extract_audio_url(entry)
                            if audio_url:
                                # Extract both plain text and HTML descriptions
                                rss_source = RSSMediaSource()
                                description, description_html = rss_source._extract_descriptions(entry)

                                episode = Episode(
                                    title=entry.get("title", "Unknown Episode"),
                                    description=description,
                                    description_html=description_html,
                                    pub_date=episode_date,
                                    audio_url=audio_url,  # type: ignore[arg-type]  # feedparser returns str, Pydantic validates to HttpUrl
                                    duration=parse_duration(entry.get("itunes_duration")),
                                    external_id=entry_external_id,
                                    raw_transcript_path=raw_transcript_path,
                                    clean_transcript_path=clean_transcript_path,
                                    summary_path=summary_path,
                                    podcast_id=podcast.id,
                                )
                                # Use targeted save methods instead of full save()
                                self.repository.save_episode(episode)
                                podcast.last_processed = datetime.now()
                                self.repository.save_podcast(podcast)
                                logger.info(f"Added and marked new episode as processed: {episode.title}")
                                return
                except Exception as e:
                    logger.error(f"Error fetching episode info for {episode_external_id}: {e}")
                    return

            # Episode update succeeded - just update podcast last_processed timestamp
            # Use save_podcast() to avoid touching episode updated_at timestamps
            podcast = self.repository.get_by_url(podcast_rss_url)
            if podcast:
                podcast.last_processed = datetime.now()
                self.repository.save_podcast(podcast)
                logger.info(f"Marked episode as processed: {episode_external_id}")

    def get_downloaded_episodes(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have downsampled audio but need transcription.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_to_transcribe = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
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
                    episodes_to_transcribe.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_to_transcribe.sort(key=lambda x: x[1].pub_date or datetime.min, reverse=True)

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
        podcasts = self.repository.get_all()

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
        podcasts = self.repository.get_all()

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

    def get_episodes_with_raw_transcripts(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have raw transcripts available for evaluation.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_with_transcripts = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if raw transcript path is set
                if not episode.raw_transcript_path:
                    continue

                # Check if raw transcript file actually exists
                if not self.path_manager.raw_transcript_file(episode.raw_transcript_path).exists():
                    continue

                episodes_with_transcripts.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_with_transcripts.sort(key=lambda x: x[1].pub_date or datetime.min, reverse=True)

        return episodes_with_transcripts

    def get_episodes_with_clean_transcripts(self, storage_path: str) -> List[Tuple[Podcast, Episode]]:
        """
        Get all episodes that have clean transcripts available for evaluation.

        Returns episodes sorted by publication date (newest first) across all podcasts,
        enabling cross-podcast prioritization when using --max-episodes.

        Args:
            storage_path: Base storage path (unused, kept for compatibility)

        Returns:
            List of (Podcast, Episode) tuples sorted by pub_date descending
        """
        episodes_with_clean = []
        podcasts = self.repository.get_all()

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if clean transcript path is set
                if not episode.clean_transcript_path:
                    continue

                # Check if clean transcript file actually exists
                if not self.path_manager.clean_transcript_file(episode.clean_transcript_path).exists():
                    continue

                episodes_with_clean.append((podcast, episode))

        # Sort by publication date (newest first) for cross-podcast prioritization
        episodes_with_clean.sort(key=lambda x: x[1].pub_date or datetime.min, reverse=True)

        return episodes_with_clean

    def list_podcasts(self) -> List[Podcast]:
        """Return list of all podcasts"""
        return self.repository.get_all()

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
