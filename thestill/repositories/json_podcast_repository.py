"""
JSON file-based implementation of podcast repository.

This implementation stores all podcasts and episodes in a single JSON file (feeds.json).
It provides the same interface as other repository implementations, making it easy
to migrate to SQLite or PostgreSQL in the future.

Migration support:
- Automatically generates UUIDs for podcasts/episodes without 'id' field
- Automatically sets 'created_at' for records without timestamp
- Migrates old 'guid' field to 'external_id' for backward compatibility
"""

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.podcast import Episode, EpisodeState, Podcast
from ..utils.path_manager import PathManager
from .podcast_repository import EpisodeRepository, PodcastRepository

logger = logging.getLogger(__name__)


class JsonPodcastRepository(PodcastRepository, EpisodeRepository):
    """
    JSON file-based implementation of podcast and episode repositories.

    Storage format:
        Single JSON file (feeds.json) containing an array of podcast objects.
        Each podcast contains a nested episodes array.

    Thread safety:
        Read operations are thread-safe.
        Write operations use atomic file replacement (write to temp, then rename).

    Example:
        repository = JsonPodcastRepository("./data")
        podcasts = repository.find_all()
        podcast = repository.find_by_url("https://example.com/feed.xml")
    """

    def __init__(self, storage_path: str):
        """
        Initialize JSON repository.

        Args:
            storage_path: Path to data storage directory
        """
        self.path_manager = PathManager(storage_path)
        self.feeds_file = self.path_manager.feeds_file()
        self._ensure_storage_exists()

    def _ensure_storage_exists(self):
        """Create storage directory and file if they don't exist."""
        self.feeds_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.feeds_file.exists():
            self._write_podcasts([])
            logger.debug(f"Created feeds file: {self.feeds_file}")

    def _migrate_podcast_data(self, podcast_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate podcast data from old format to new format.

        Handles:
        - Adding 'id' field (UUID) if missing
        - Adding 'created_at' field if missing
        - Migrating episodes' 'guid' → 'external_id'
        - Adding episode 'id' and 'created_at' if missing

        Args:
            podcast_data: Raw podcast dictionary from JSON

        Returns:
            Migrated podcast dictionary
        """
        migrated = False

        # Migrate podcast-level fields
        if "id" not in podcast_data:
            podcast_data["id"] = str(uuid.uuid4())
            migrated = True
            logger.debug(f"Generated UUID for podcast: {podcast_data.get('title', 'unknown')}")

        if "created_at" not in podcast_data:
            podcast_data["created_at"] = datetime.utcnow().isoformat()
            migrated = True
            logger.debug(f"Set created_at for podcast: {podcast_data.get('title', 'unknown')}")

        # Migrate episodes
        if "episodes" in podcast_data:
            for episode in podcast_data["episodes"]:
                # Migrate guid → external_id
                if "guid" in episode and "external_id" not in episode:
                    episode["external_id"] = episode.pop("guid")
                    migrated = True

                # Generate UUID if missing
                if "id" not in episode:
                    episode["id"] = str(uuid.uuid4())
                    migrated = True

                # Set created_at if missing
                if "created_at" not in episode:
                    episode["created_at"] = datetime.utcnow().isoformat()
                    migrated = True

        return podcast_data

    def _read_podcasts(self) -> List[Podcast]:
        """
        Read all podcasts from JSON file with automatic migration.

        Automatically migrates old data format to new format:
        - Generates UUIDs for podcasts/episodes without 'id'
        - Sets 'created_at' timestamps if missing
        - Migrates 'guid' → 'external_id' in episodes

        Returns:
            List of podcasts, or empty list if file doesn't exist or is invalid

        Note:
            This method handles errors gracefully and returns empty list on failure.
            Errors are logged but not raised.
            Migration happens automatically and is idempotent.
        """
        try:
            with open(self.feeds_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check if migration is needed
            needs_migration = False
            for podcast_data in data:
                if "id" not in podcast_data or "created_at" not in podcast_data:
                    needs_migration = True
                    break
                if "episodes" in podcast_data:
                    for episode in podcast_data["episodes"]:
                        if "guid" in episode or "id" not in episode or "created_at" not in episode:
                            needs_migration = True
                            break

            # Create backup before migration
            if needs_migration and self.feeds_file.exists():
                backup_file = self.feeds_file.with_suffix(".backup.json")
                shutil.copy2(self.feeds_file, backup_file)
                logger.info(f"Created backup before migration: {backup_file}")

            # Migrate data
            migrated_data = []
            for podcast_data in data:
                migrated_podcast = self._migrate_podcast_data(podcast_data)
                migrated_data.append(migrated_podcast)

            # Save migrated data if changes were made
            if needs_migration:
                self._write_podcasts([Podcast(**p) for p in migrated_data])
                logger.info("Migration complete: Updated feeds.json with new format")

            # Parse and return podcasts
            return [Podcast(**podcast_data) for podcast_data in migrated_data]

        except FileNotFoundError:
            logger.debug(f"Feeds file not found: {self.feeds_file}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in feeds file: {e}")
            return []
        except (TypeError, ValueError) as e:
            logger.error(f"Error parsing podcast data: {e}")
            return []

    def _write_podcasts(self, podcasts: List[Podcast]):
        """
        Write all podcasts to JSON file.

        Uses atomic write strategy:
        1. Write to temporary file
        2. Rename to target file (atomic operation on POSIX systems)

        Args:
            podcasts: List of podcasts to write

        Raises:
            OSError: If file write fails
            ValueError: If podcast serialization fails
        """
        try:
            # Write to temporary file first (atomic operation)
            temp_file = self.feeds_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump([p.model_dump(mode="json") for p in podcasts], f, indent=2, ensure_ascii=False)

            # Atomic rename (on POSIX systems)
            temp_file.replace(self.feeds_file)
            logger.debug(f"Successfully wrote {len(podcasts)} podcasts to {self.feeds_file}")

        except (OSError, IOError) as e:
            logger.error(f"Error writing podcasts to file: {e}")
            # Clean up temp file if it exists
            if temp_file.exists():
                temp_file.unlink()
            raise
        except (TypeError, ValueError) as e:
            logger.error(f"Error serializing podcasts: {e}")
            raise

    # Implement PodcastRepository interface

    def find_all(self) -> List[Podcast]:
        """
        Retrieve all podcasts.

        Returns:
            List of all podcasts, ordered by insertion (oldest first)
        """
        return self._read_podcasts()

    def find_by_id(self, podcast_id: str) -> Optional[Podcast]:
        """
        Find podcast by internal UUID.

        Args:
            podcast_id: Internal UUID of the podcast

        Returns:
            Podcast if found, None otherwise
        """
        podcasts = self._read_podcasts()
        for podcast in podcasts:
            if podcast.id == podcast_id:
                return podcast
        return None

    def find_by_index(self, index: int) -> Optional[Podcast]:
        """
        Find podcast by 1-based index (for CLI/user convenience).

        Args:
            index: 1-based index (human-friendly ID)

        Returns:
            Podcast if found, None otherwise
        """
        podcasts = self._read_podcasts()
        if 1 <= index <= len(podcasts):
            return podcasts[index - 1]
        return None

    def find_by_url(self, url: str) -> Optional[Podcast]:
        """
        Find podcast by RSS URL.

        Args:
            url: RSS feed URL (unique identifier)

        Returns:
            Podcast if found, None otherwise
        """
        podcasts = self._read_podcasts()
        for podcast in podcasts:
            if str(podcast.rss_url) == url:
                return podcast
        return None

    def exists(self, url: str) -> bool:
        """
        Check if podcast with given URL exists.

        Args:
            url: RSS feed URL

        Returns:
            True if podcast exists, False otherwise
        """
        return self.find_by_url(url) is not None

    def save(self, podcast: Podcast) -> Podcast:
        """
        Save or update a podcast.

        If a podcast with the same URL already exists, it will be updated in place.
        Otherwise, the new podcast will be appended to the list.

        Args:
            podcast: Podcast to save or update

        Returns:
            The saved podcast

        Raises:
            OSError: If file write fails
        """
        podcasts = self._read_podcasts()

        # Check if podcast already exists (by URL)
        existing_index = None
        for i, p in enumerate(podcasts):
            if str(p.rss_url) == str(podcast.rss_url):
                existing_index = i
                break

        if existing_index is not None:
            # Update existing podcast
            podcasts[existing_index] = podcast
            logger.debug(f"Updated existing podcast: {podcast.title}")
        else:
            # Add new podcast
            podcasts.append(podcast)
            logger.debug(f"Added new podcast: {podcast.title}")

        self._write_podcasts(podcasts)
        return podcast

    def delete(self, url: str) -> bool:
        """
        Delete podcast by URL.

        Args:
            url: RSS feed URL of podcast to delete

        Returns:
            True if podcast was deleted, False if not found
        """
        podcasts = self._read_podcasts()
        initial_count = len(podcasts)

        # Filter out podcast with matching URL
        podcasts = [p for p in podcasts if str(p.rss_url) != url]

        if len(podcasts) < initial_count:
            self._write_podcasts(podcasts)
            logger.info(f"Deleted podcast with URL: {url}")
            return True

        logger.debug(f"Podcast not found for deletion: {url}")
        return False

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
                {"audio_path": "/path/to/audio.mp3"}
            )
        """
        podcasts = self._read_podcasts()

        # Find the podcast
        for podcast in podcasts:
            if str(podcast.rss_url) != podcast_url:
                continue

            # Find the episode within the podcast
            for episode in podcast.episodes:
                if episode.external_id != episode_external_id:
                    continue

                # Update episode fields
                updated_fields = []
                for field, value in updates.items():
                    if hasattr(episode, field):
                        setattr(episode, field, value)
                        updated_fields.append(field)
                    else:
                        logger.warning(f"Unknown episode field: {field}")

                if updated_fields:
                    self._write_podcasts(podcasts)
                    logger.debug(f"Updated episode {episode_external_id}: {', '.join(updated_fields)}")
                    return True
                else:
                    logger.debug(f"No valid fields to update for episode {episode_external_id}")
                    return False

        logger.debug(f"Episode not found: {episode_external_id} in {podcast_url}")
        return False

    # Implement EpisodeRepository interface

    def find_by_podcast(self, podcast_url: str) -> List[Episode]:
        """
        Get all episodes for a podcast.

        Args:
            podcast_url: RSS feed URL of the podcast

        Returns:
            List of episodes for the podcast, or empty list if podcast not found
        """
        podcast = self.find_by_url(podcast_url)
        return podcast.episodes if podcast else []

    def find_by_id(self, episode_id: str) -> Optional[tuple[Podcast, Episode]]:
        """
        Find episode by internal UUID.

        Args:
            episode_id: Internal UUID of the episode

        Returns:
            Tuple of (Podcast, Episode) if found, None otherwise
        """
        podcasts = self._read_podcasts()
        for podcast in podcasts:
            for episode in podcast.episodes:
                if episode.id == episode_id:
                    return (podcast, episode)
        return None

    def find_by_external_id(self, podcast_url: str, episode_external_id: str) -> Optional[Episode]:
        """
        Find specific episode by external ID (from RSS feed).

        Args:
            podcast_url: RSS feed URL of the podcast
            episode_external_id: External ID of the episode (publisher's GUID)

        Returns:
            Episode if found, None otherwise
        """
        episodes = self.find_by_podcast(podcast_url)
        for episode in episodes:
            if episode.external_id == episode_external_id:
                return episode
        return None

    def find_unprocessed(self, state: str) -> List[tuple[Podcast, Episode]]:
        """
        Find episodes in specific processing state.

        This method is used to find episodes that need processing at each
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
            # Find all episodes ready for download
            episodes_to_download = repository.find_unprocessed('discovered')
            for podcast, episode in episodes_to_download:
                download_audio(podcast, episode)
        """
        podcasts = self._read_podcasts()
        results = []

        for podcast in podcasts:
            for episode in podcast.episodes:
                # Check if episode is in the specified state
                # Episodes are considered "unprocessed" for a state if they're
                # currently in that state (not yet advanced to the next state)
                matches = False

                if state == EpisodeState.DISCOVERED.value:
                    matches = episode.state == EpisodeState.DISCOVERED
                elif state == EpisodeState.DOWNLOADED.value:
                    matches = episode.state == EpisodeState.DOWNLOADED
                elif state == EpisodeState.DOWNSAMPLED.value:
                    matches = episode.state == EpisodeState.DOWNSAMPLED
                elif state == EpisodeState.TRANSCRIBED.value:
                    matches = episode.state == EpisodeState.TRANSCRIBED
                else:
                    logger.warning(f"Unknown processing state: {state}")

                if matches:
                    results.append((podcast, episode))

        return results
