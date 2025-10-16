"""
Abstract repository interfaces for podcast and episode persistence.

These interfaces define contracts that all concrete implementations must follow,
enabling easy swapping between different storage backends (JSON, SQLite, PostgreSQL, etc.).
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models.podcast import Episode, Podcast


class PodcastRepository(ABC):
    """
    Abstract repository for podcast persistence operations.

    Implementations must provide thread-safe access to podcast data.
    """

    @abstractmethod
    def get_all(self) -> List[Podcast]:
        """
        Get all podcasts.

        Returns:
            List of all podcasts, ordered by insertion (oldest first)
        """
        pass

    @abstractmethod
    def get(self, podcast_id: str) -> Optional[Podcast]:
        """
        Get podcast by internal UUID (primary key).

        Args:
            podcast_id: Internal UUID of the podcast

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_index(self, index: int) -> Optional[Podcast]:
        """
        Get podcast by 1-based index (for CLI/user convenience).

        Args:
            index: 1-based index (human-friendly ID)

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_url(self, url: str) -> Optional[Podcast]:
        """
        Get podcast by RSS URL (unique external identifier).

        Args:
            url: RSS feed URL (unique identifier)

        Returns:
            Podcast if found, None otherwise
        """
        pass

    @abstractmethod
    def exists(self, url: str) -> bool:
        """
        Check if podcast with given URL exists.

        Args:
            url: RSS feed URL

        Returns:
            True if podcast exists, False otherwise
        """
        pass

    @abstractmethod
    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update a podcast.

        If a podcast with the same URL already exists, it will be updated.
        Otherwise, a new podcast will be created.

        Args:
            podcast: Podcast to save or update

        Returns:
            The saved podcast (may include generated fields)
        """
        pass

    @abstractmethod
    def delete(self, url: str) -> bool:
        """
        Delete podcast by URL.

        Args:
            url: RSS feed URL of podcast to delete

        Returns:
            True if podcast was deleted, False if not found
        """
        pass

    @abstractmethod
    def update_episode(self, podcast_url: str, episode_external_id: str, updates: dict) -> bool:
        """
        Update specific episode fields.

        This method allows atomic updates to episode fields without
        requiring a full podcast save operation.

        Args:
            podcast_url: URL of the podcast containing the episode
            episode_external_id: External ID (from RSS feed) of the episode to update
            updates: Dictionary of field names and new values

        Returns:
            True if episode was found and updated, False otherwise

        Example:
            repository.update_episode(
                "https://example.com/feed.xml",
                "episode-123",
                {"audio_path": "/path/to/audio.mp3", "audio_size": 1024000}
            )
        """
        pass


class EpisodeRepository(ABC):
    """
    Abstract repository for episode-specific queries.

    This interface provides episode-focused operations that may be more
    efficient than loading full podcast objects.
    """

    @abstractmethod
    def get_episodes_by_podcast(self, podcast_url: str) -> List[Episode]:
        """
        Get all episodes for a podcast.

        Args:
            podcast_url: RSS feed URL of the podcast

        Returns:
            List of episodes for the podcast, ordered by pub_date (newest first)
        """
        pass

    @abstractmethod
    def get_episode(self, episode_id: str) -> Optional[tuple[Podcast, Episode]]:
        """
        Get episode by internal UUID (primary key).

        Args:
            episode_id: Internal UUID of the episode

        Returns:
            Tuple of (Podcast, Episode) if found, None otherwise
        """
        pass

    @abstractmethod
    def get_episode_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """
        Get specific episode by external ID (from RSS feed).

        Args:
            podcast_url: RSS feed URL of the podcast
            episode_external_id: External ID of the episode (publisher's GUID)

        Returns:
            Episode if found, None otherwise
        """
        pass

    @abstractmethod
    def get_unprocessed_episodes(self, state: str) -> List[tuple[Podcast, Episode]]:
        """
        Get episodes in specific processing state.

        This method is used to get episodes that need processing at each
        stage of the pipeline (download, downsample, transcribe, clean).

        Args:
            state: Processing state to filter by. Valid values:
                - 'discovered': Has audio_url but no audio_path
                - 'downloaded': Has audio_path but no downsampled_audio_path
                - 'downsampled': Has downsampled_audio_path but no raw_transcript_path
                - 'transcribed': Has raw_transcript_path but no clean_transcript_path

        Returns:
            List of (Podcast, Episode) tuples matching the state

        Example:
            # Get all episodes ready for download
            episodes_to_download = repository.get_unprocessed_episodes('discovered')
            for podcast, episode in episodes_to_download:
                download_audio(podcast, episode)
        """
        pass
