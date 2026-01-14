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

"""
Podcast service - Business logic for podcast and episode management
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Literal, NamedTuple, Optional, Union

from pydantic import BaseModel, computed_field

from ..core.feed_manager import PodcastFeedManager
from ..models.podcast import Episode, Podcast
from ..repositories.podcast_repository import PodcastRepository
from ..utils.duration import format_duration
from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)

# Type alias for transcript type
TranscriptType = Literal["cleaned", "raw"]


class TranscriptResult(NamedTuple):
    """Result from get_transcript with type information"""

    content: str
    transcript_type: Optional[TranscriptType]  # None if N/A message


class PodcastWithIndex(BaseModel):
    """Podcast with human-friendly index number"""

    index: int
    title: str
    description: str
    rss_url: str
    slug: str
    image_url: Optional[str] = None
    language: str = "en"  # ISO 639-1 language code
    last_processed: Optional[datetime] = None
    episodes_count: int = 0
    episodes_processed: int = 0

    @computed_field  # type: ignore[misc]
    @property
    def description_text(self) -> str:
        """Plain text version of description (HTML stripped)"""
        from thestill.utils.html_utils import html_to_plain_text

        return html_to_plain_text(self.description)


class EpisodeWithIndex(BaseModel):
    """Episode with human-friendly index numbers"""

    id: str  # Internal UUID for direct access
    podcast_index: int
    podcast_slug: str
    episode_index: int
    title: str
    slug: str
    description: str
    pub_date: Optional[datetime] = None
    audio_url: str
    duration: Optional[int] = None  # Duration in seconds
    external_id: str  # External ID from RSS feed (publisher's GUID)
    state: str  # Processing state (discovered, downloaded, downsampled, transcribed, cleaned)
    transcript_available: bool = False
    summary_available: bool = False
    image_url: Optional[str] = None  # Episode-specific artwork

    @computed_field  # type: ignore[misc]
    @property
    def duration_formatted(self) -> Optional[str]:
        """Human-readable duration (e.g., '1:08:01' or '45:30')"""
        if self.duration is None:
            return None
        return format_duration(self.duration)


class PodcastService:
    """
    Service for podcast and episode management with flexible ID resolution.

    Podcast ID formats supported:
    - Integer index (1, 2, 3...) - 1-based indexing
    - RSS URL string

    Episode ID formats supported:
    - Integer index (1, 2, 3...) - 1=latest, 2=second latest, etc.
    - "latest" keyword - most recent episode
    - Date string (YYYY-MM-DD) - match by publish date
    - GUID string - exact match

    Attributes:
        storage_path: Path to data storage directory
        path_manager: Path manager for file operations
        repository: Repository for podcast persistence
        feed_manager: Feed manager for RSS operations
    """

    def __init__(
        self,
        storage_path: Union[str, Path],
        podcast_repository: PodcastRepository,
        path_manager: PathManager,
    ) -> None:
        """
        Initialize podcast service.

        Args:
            storage_path: Path to data storage directory (str or Path)
            podcast_repository: Repository for podcast persistence
            path_manager: Path manager for file path operations
        """
        self.storage_path: Path = Path(storage_path) if isinstance(storage_path, str) else storage_path
        self.path_manager: PathManager = path_manager
        self.repository: PodcastRepository = podcast_repository

        # Initialize FeedManager with repository and path manager
        self.feed_manager: PodcastFeedManager = PodcastFeedManager(
            podcast_repository=podcast_repository, path_manager=path_manager
        )

        logger.info(f"PodcastService initialized with storage: {self.storage_path}")

    def add_podcast(self, url: str) -> Optional[Podcast]:
        """
        Add a new podcast to tracking.

        Args:
            url: RSS URL, Apple Podcast URL, or YouTube channel/playlist URL

        Returns:
            Podcast object if successful, None if failed
        """
        logger.info(f"Adding podcast: {url}")
        success = self.feed_manager.add_podcast(url)

        if success:
            # Retrieve the added podcast (it will be the last one added)
            podcasts = self.feed_manager.list_podcasts()
            if podcasts:
                # The newly added podcast is the last one in the list
                added_podcast = podcasts[-1]
                logger.info(f"Successfully added podcast: {added_podcast.title}")
                return added_podcast

        logger.warning(f"Failed to add podcast or already exists: {url}")
        return None

    def remove_podcast(self, podcast_id: Union[str, int]) -> bool:
        """
        Remove a podcast from tracking.

        Args:
            podcast_id: Podcast index (int) or RSS URL (str)

        Returns:
            True if removed, False if not found
        """
        # Resolve podcast ID to RSS URL
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found: {podcast_id}")
            return False

        rss_url = str(podcast.rss_url)
        logger.info(f"Removing podcast: {podcast.title}")
        return self.feed_manager.remove_podcast(rss_url)

    def get_podcasts(self) -> List[PodcastWithIndex]:
        """
        Get all tracked podcasts with index numbers.

        Returns:
            List of podcasts with human-friendly indices
        """
        podcasts = self.feed_manager.list_podcasts()
        logger.debug(f"Listing {len(podcasts)} podcasts")

        from ..models.podcast import EpisodeState

        result = []
        for idx, podcast in enumerate(podcasts, start=1):
            # Count episodes that have completed the cleaning pipeline (CLEANED or SUMMARIZED)
            episodes_processed = sum(
                1 for ep in podcast.episodes if ep.state in (EpisodeState.CLEANED, EpisodeState.SUMMARIZED)
            )
            result.append(
                PodcastWithIndex(
                    index=idx,
                    title=podcast.title,
                    description=podcast.description,
                    rss_url=str(podcast.rss_url),
                    slug=podcast.slug,
                    image_url=podcast.image_url,
                    language=podcast.language,
                    last_processed=podcast.last_processed,
                    episodes_count=len(podcast.episodes),
                    episodes_processed=episodes_processed,
                )
            )

        return result

    def get_podcast(self, podcast_id: Union[str, int]) -> Optional[Podcast]:
        """
        Get a podcast by ID.

        Args:
            podcast_id: Integer index (1-based), slug, RSS URL string, or UUID string

        Returns:
            Podcast object or None if not found
        """
        podcasts = self.feed_manager.list_podcasts()

        # If integer, treat as index (1-based)
        if isinstance(podcast_id, int):
            if 1 <= podcast_id <= len(podcasts):
                logger.debug(f"Retrieved podcast by index: {podcast_id}")
                return podcasts[podcast_id - 1]
            logger.warning(f"Podcast index out of range: {podcast_id}")
            return None

        # If string that looks like a number, convert to int
        if isinstance(podcast_id, str) and podcast_id.isdigit():
            return self.get_podcast(int(podcast_id))

        # Check if it's a UUID (internal ID)
        if isinstance(podcast_id, str) and len(podcast_id) == 36 and podcast_id.count("-") == 4:
            for podcast in podcasts:
                if podcast.id == podcast_id:
                    logger.debug(f"Retrieved podcast by UUID: {podcast.title}")
                    return podcast

        # Check if it's a slug (URL-safe identifier)
        if isinstance(podcast_id, str):
            for podcast in podcasts:
                if podcast.slug == podcast_id:
                    logger.debug(f"Retrieved podcast by slug: {podcast.title}")
                    return podcast

        # Otherwise, treat as RSS URL
        for podcast in podcasts:
            if str(podcast.rss_url) == podcast_id:
                logger.debug(f"Retrieved podcast by URL: {podcast.title}")
                return podcast

        logger.warning(f"Podcast not found: {podcast_id}")
        return None

    def get_episode(self, podcast_id: Union[str, int], episode_id: Union[str, int]) -> Optional[Episode]:
        """
        Get an episode by podcast ID and episode ID.

        Args:
            podcast_id: Podcast index, RSS URL, or UUID
            episode_id: Episode index (1=latest), 'latest', date (YYYY-MM-DD), UUID, or external ID

        Returns:
            Episode object or None if not found
        """
        # First, get the podcast
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found for episode lookup: {podcast_id}")
            return None

        if not podcast.episodes:
            logger.warning(f"No episodes found for podcast: {podcast.title}")
            return None

        # Sort episodes by pub_date descending (latest first)
        sorted_episodes = sorted(podcast.episodes, key=lambda ep: ep.pub_date or datetime.min, reverse=True)

        # Handle "latest" keyword
        if episode_id == "latest":
            logger.debug(f"Retrieved latest episode from: {podcast.title}")
            return sorted_episodes[0]

        # Handle integer index (1=latest, 2=second latest, etc.)
        if isinstance(episode_id, int):
            if 1 <= episode_id <= len(sorted_episodes):
                logger.debug(f"Retrieved episode by index {episode_id} from: {podcast.title}")
                return sorted_episodes[episode_id - 1]
            logger.warning(f"Episode index out of range: {episode_id}")
            return None

        # If string that looks like a number, convert to int
        if isinstance(episode_id, str) and episode_id.isdigit():
            return self.get_episode(podcast_id, int(episode_id))

        # Handle date format (YYYY-MM-DD)
        if isinstance(episode_id, str) and len(episode_id) == 10 and episode_id.count("-") == 2:
            try:
                target_date = datetime.fromisoformat(episode_id).date()
                for episode in sorted_episodes:
                    if episode.pub_date and episode.pub_date.date() == target_date:
                        logger.debug(f"Retrieved episode by date {episode_id}: {episode.title}")
                        return episode
                logger.warning(f"No episode found for date: {episode_id}")
                return None
            except ValueError:
                pass  # Not a valid date, continue to UUID/GUID matching

        # Check if it's a UUID (internal ID)
        if isinstance(episode_id, str) and len(episode_id) == 36 and episode_id.count("-") == 4:
            for episode in podcast.episodes:
                if episode.id == episode_id:
                    logger.debug(f"Retrieved episode by UUID: {episode.title}")
                    return episode

        # Otherwise, treat as external ID (GUID from RSS feed)
        for episode in podcast.episodes:
            if episode.external_id == episode_id:
                logger.debug(f"Retrieved episode by external ID: {episode.title}")
                return episode

        logger.warning(f"Episode not found: {episode_id}")
        return None

    def get_episodes(
        self,
        podcast_id: Union[str, int],
        limit: int = 100,
        offset: int = 0,
        since_hours: Optional[int] = None,
    ) -> Optional[List[EpisodeWithIndex]]:
        """
        Get episodes for a podcast with optional filtering and pagination.

        Args:
            podcast_id: Podcast index or RSS URL
            limit: Maximum number of episodes to return (default 100)
            offset: Number of episodes to skip (default 0)
            since_hours: Only include episodes published in last N hours

        Returns:
            List of episodes with indices, or None if podcast not found
        """
        # Get the podcast
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            logger.warning(f"Podcast not found for episode listing: {podcast_id}")
            return None

        # Get podcast index for response
        podcasts = self.get_podcasts()
        podcast_index = next((p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)), 0)

        # Sort episodes by pub_date descending (latest first)
        sorted_episodes = sorted(podcast.episodes, key=lambda ep: ep.pub_date or datetime.min, reverse=True)

        # Filter by date if since_hours specified
        if since_hours is not None:
            cutoff_time = datetime.now() - timedelta(hours=since_hours)
            sorted_episodes = [ep for ep in sorted_episodes if ep.pub_date and ep.pub_date >= cutoff_time]
            logger.debug(f"Filtered to {len(sorted_episodes)} episodes from last {since_hours}h")

        # Apply offset and limit
        sorted_episodes = sorted_episodes[offset : offset + limit]

        # Build result with indices (account for offset in indexing)
        result = []
        for idx, episode in enumerate(sorted_episodes, start=offset + 1):
            result.append(
                EpisodeWithIndex(
                    id=episode.id,
                    podcast_index=podcast_index,
                    podcast_slug=podcast.slug,
                    episode_index=idx,
                    title=episode.title,
                    slug=episode.slug,
                    description=episode.description,
                    pub_date=episode.pub_date,
                    audio_url=str(episode.audio_url),
                    duration=episode.duration,
                    external_id=episode.external_id,
                    state=episode.state.value,
                    transcript_available=bool(
                        episode.clean_transcript_path
                        and self.path_manager.clean_transcript_file(episode.clean_transcript_path).exists()
                    ),
                    summary_available=bool(
                        episode.summary_path and self.path_manager.summary_file(episode.summary_path).exists()
                    ),
                    image_url=episode.image_url,
                )
            )

        logger.debug(f"Listed {len(result)} episodes from: {podcast.title}")
        return result

    def get_episodes_count(self, podcast_id: Union[str, int], since_hours: Optional[int] = None) -> Optional[int]:
        """
        Get total count of episodes for a podcast.

        Args:
            podcast_id: Podcast index or RSS URL
            since_hours: Only count episodes published in last N hours

        Returns:
            Total episode count, or None if podcast not found
        """
        podcast = self.get_podcast(podcast_id)
        if not podcast:
            return None

        if since_hours is not None:
            cutoff_time = datetime.now() - timedelta(hours=since_hours)
            return sum(1 for ep in podcast.episodes if ep.pub_date and ep.pub_date >= cutoff_time)

        return len(podcast.episodes)

    def get_transcript(self, podcast_id: Union[str, int], episode_id: Union[str, int]) -> Optional[TranscriptResult]:
        """
        Get the transcript for an episode, preferring cleaned over raw.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index, 'latest', date, or GUID

        Returns:
            TranscriptResult with content and type, or None if episode not found
        """
        episode = self.get_episode(podcast_id, episode_id)
        if not episode:
            logger.warning(f"Episode not found for transcript: {podcast_id}/{episode_id}")
            return None

        from ..models.podcast import EpisodeState

        # Try cleaned transcript first (preferred)
        if episode.clean_transcript_path:
            md_path = self.path_manager.clean_transcript_file(episode.clean_transcript_path)
            try:
                self.path_manager.require_file_exists(md_path, "Cleaned transcript file not found")
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"Retrieved cleaned transcript for: {episode.title}")
                return TranscriptResult(content=content, transcript_type="cleaned")
            except FileNotFoundError:
                logger.warning(f"Cleaned transcript file not found: {md_path}")
            except Exception as e:
                logger.error(f"Error reading cleaned transcript file: {e}")

        # Fall back to raw transcript if available
        if episode.raw_transcript_path:
            json_path = self.path_manager.raw_transcript_file(episode.raw_transcript_path)
            try:
                import json

                from ..core.transcript_formatter import TranscriptFormatter

                self.path_manager.require_file_exists(json_path, "Raw transcript file not found")
                with open(json_path, "r", encoding="utf-8") as f:
                    transcript_data = json.load(f)
                # Convert raw JSON to Markdown for display
                formatter = TranscriptFormatter()
                content = formatter.format_transcript(transcript_data)
                logger.info(f"Retrieved raw transcript for: {episode.title}")
                return TranscriptResult(content=content, transcript_type="raw")
            except FileNotFoundError:
                logger.warning(f"Raw transcript file not found: {json_path}")
            except Exception as e:
                logger.error(f"Error reading raw transcript file: {e}")

        # No transcript available
        logger.info(f"No transcript available for: {episode.title}")
        return TranscriptResult(content="N/A - No transcript available", transcript_type=None)

    def get_summary(self, podcast_id: Union[str, int], episode_id: Union[str, int]) -> Optional[str]:
        """
        Get the summary for an episode.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index, 'latest', date, or GUID

        Returns:
            Summary Markdown content, "N/A" message, or None if episode not found
        """
        episode = self.get_episode(podcast_id, episode_id)
        if not episode:
            logger.warning(f"Episode not found for summary: {podcast_id}/{episode_id}")
            return None

        # Check if episode has a summary
        if not episode.summary_path:
            logger.info(f"Episode not yet summarized: {episode.title}")
            return "N/A - Episode not yet summarized"

        # Build full path to the summary file using PathManager
        summary_path = self.path_manager.summary_file(episode.summary_path)

        # Verify summary file exists
        try:
            self.path_manager.require_file_exists(summary_path, "Summary file not found")
        except FileNotFoundError:
            logger.warning(f"Summary file not found: {summary_path}")
            return "N/A - Summary file not found"

        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                content = f.read()
            logger.info(f"Retrieved summary for: {episode.title}")
            return content
        except Exception as e:
            logger.error(f"Error reading summary file: {e}")
            return f"N/A - Error reading summary: {e}"
