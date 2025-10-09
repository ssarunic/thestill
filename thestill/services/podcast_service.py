"""
Podcast service - Business logic for podcast and episode management
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel

from ..core.feed_manager import PodcastFeedManager
from ..models.podcast import Podcast, Episode

logger = logging.getLogger(__name__)


class PodcastWithIndex(BaseModel):
    """Podcast with human-friendly index number"""
    index: int
    title: str
    description: str
    rss_url: str
    last_processed: Optional[datetime] = None
    episodes_count: int = 0
    episodes_processed: int = 0


class EpisodeWithIndex(BaseModel):
    """Episode with human-friendly index numbers"""
    podcast_index: int
    episode_index: int
    title: str
    description: str
    pub_date: Optional[datetime] = None
    audio_url: str
    duration: Optional[str] = None
    guid: str
    processed: bool = False
    transcript_available: bool = False
    summary_available: bool = False


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
    """

    def __init__(self, storage_path: str):
        """
        Initialize podcast service.

        Args:
            storage_path: Path to data storage directory
        """
        self.storage_path = Path(storage_path)
        self.feed_manager = PodcastFeedManager(str(storage_path))
        logger.info(f"PodcastService initialized with storage: {storage_path}")

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
            # Retrieve the added podcast
            podcasts = self.feed_manager.list_podcasts()
            for podcast in podcasts:
                if str(podcast.rss_url) == url:
                    logger.info(f"Successfully added podcast: {podcast.title}")
                    return podcast

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

    def list_podcasts(self) -> List[PodcastWithIndex]:
        """
        List all tracked podcasts with index numbers.

        Returns:
            List of podcasts with human-friendly indices
        """
        podcasts = self.feed_manager.list_podcasts()
        logger.debug(f"Listing {len(podcasts)} podcasts")

        result = []
        for idx, podcast in enumerate(podcasts, start=1):
            episodes_processed = sum(1 for ep in podcast.episodes if ep.processed)
            result.append(PodcastWithIndex(
                index=idx,
                title=podcast.title,
                description=podcast.description,
                rss_url=str(podcast.rss_url),
                last_processed=podcast.last_processed,
                episodes_count=len(podcast.episodes),
                episodes_processed=episodes_processed
            ))

        return result

    def get_podcast(self, podcast_id: Union[str, int]) -> Optional[Podcast]:
        """
        Get a podcast by ID.

        Args:
            podcast_id: Integer index (1-based) or RSS URL string

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

        # Otherwise, treat as RSS URL
        for podcast in podcasts:
            if str(podcast.rss_url) == podcast_id:
                logger.debug(f"Retrieved podcast by URL: {podcast.title}")
                return podcast

        logger.warning(f"Podcast not found: {podcast_id}")
        return None

    def get_episode(
        self,
        podcast_id: Union[str, int],
        episode_id: Union[str, int]
    ) -> Optional[Episode]:
        """
        Get an episode by podcast ID and episode ID.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index (1=latest), 'latest', date (YYYY-MM-DD), or GUID

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
        sorted_episodes = sorted(
            podcast.episodes,
            key=lambda ep: ep.pub_date or datetime.min,
            reverse=True
        )

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
        if isinstance(episode_id, str) and len(episode_id) == 10 and episode_id.count('-') == 2:
            try:
                target_date = datetime.fromisoformat(episode_id).date()
                for episode in sorted_episodes:
                    if episode.pub_date and episode.pub_date.date() == target_date:
                        logger.debug(f"Retrieved episode by date {episode_id}: {episode.title}")
                        return episode
                logger.warning(f"No episode found for date: {episode_id}")
                return None
            except ValueError:
                pass  # Not a valid date, continue to GUID matching

        # Otherwise, treat as GUID
        for episode in podcast.episodes:
            if episode.guid == episode_id:
                logger.debug(f"Retrieved episode by GUID: {episode.title}")
                return episode

        logger.warning(f"Episode not found: {episode_id}")
        return None

    def list_episodes(
        self,
        podcast_id: Union[str, int],
        limit: int = 10,
        since_hours: Optional[int] = None
    ) -> Optional[List[EpisodeWithIndex]]:
        """
        List episodes for a podcast with optional filtering.

        Args:
            podcast_id: Podcast index or RSS URL
            limit: Maximum number of episodes to return
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
        podcasts = self.list_podcasts()
        podcast_index = next(
            (p.index for p in podcasts if str(p.rss_url) == str(podcast.rss_url)),
            0
        )

        # Sort episodes by pub_date descending (latest first)
        sorted_episodes = sorted(
            podcast.episodes,
            key=lambda ep: ep.pub_date or datetime.min,
            reverse=True
        )

        # Filter by date if since_hours specified
        if since_hours is not None:
            cutoff_time = datetime.now() - timedelta(hours=since_hours)
            sorted_episodes = [
                ep for ep in sorted_episodes
                if ep.pub_date and ep.pub_date >= cutoff_time
            ]
            logger.debug(f"Filtered to {len(sorted_episodes)} episodes from last {since_hours}h")

        # Apply limit
        sorted_episodes = sorted_episodes[:limit]

        # Build result with indices
        result = []
        for idx, episode in enumerate(sorted_episodes, start=1):
            result.append(EpisodeWithIndex(
                podcast_index=podcast_index,
                episode_index=idx,
                title=episode.title,
                description=episode.description,
                pub_date=episode.pub_date,
                audio_url=str(episode.audio_url),
                duration=episode.duration,
                guid=episode.guid,
                processed=episode.processed,
                transcript_available=bool(episode.transcript_path and (self.storage_path / "transcripts" / episode.transcript_path).exists()),
                summary_available=bool(episode.summary_path and (self.storage_path / "processed" / episode.summary_path).exists())
            ))

        logger.debug(f"Listed {len(result)} episodes from: {podcast.title}")
        return result

    def get_transcript(
        self,
        podcast_id: Union[str, int],
        episode_id: Union[str, int]
    ) -> Optional[str]:
        """
        Get the cleaned transcript for an episode.

        Args:
            podcast_id: Podcast index or RSS URL
            episode_id: Episode index, 'latest', date, or GUID

        Returns:
            Cleaned Markdown transcript, "N/A" message, or None if episode not found
        """
        episode = self.get_episode(podcast_id, episode_id)
        if not episode:
            logger.warning(f"Episode not found for transcript: {podcast_id}/{episode_id}")
            return None

        # Check if processed and has summary (cleaned transcript)
        if not episode.processed or not episode.summary_path:
            logger.info(f"Episode not yet processed: {episode.title}")
            return "N/A - Episode not yet processed"

        # Build full path to the cleaned Markdown file
        # summary_path is just the filename (e.g., "episode_cleaned.md")
        md_path = self.storage_path / "processed" / episode.summary_path

        if not md_path.exists():
            logger.warning(f"Cleaned transcript file not found: {md_path}")
            return "N/A - Transcript file not found"

        try:
            with open(md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            logger.info(f"Retrieved transcript for: {episode.title}")
            return content
        except Exception as e:
            logger.error(f"Error reading transcript file: {e}")
            return f"N/A - Error reading transcript: {e}"
