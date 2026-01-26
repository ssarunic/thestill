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
Stats service - System statistics and status information
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from pydantic import BaseModel
from structlog import get_logger

from ..repositories.podcast_repository import PodcastRepository
from ..utils.path_manager import PathManager

logger = get_logger(__name__)


class ActivityItem(BaseModel):
    """Recent activity item for tracking episode state changes."""

    episode_id: str
    episode_title: str
    podcast_title: str
    podcast_id: str
    state: str  # EpisodeState value (discovered, downloaded, etc.)
    timestamp: datetime  # episode.updated_at
    pub_date: Optional[datetime] = None


class SystemStats(BaseModel):
    """System-wide statistics"""

    podcasts_tracked: int
    episodes_total: int

    # Episode counts by state (pipeline progression)
    episodes_discovered: int  # Not yet downloaded
    episodes_downloaded: int  # Downloaded but not downsampled
    episodes_downsampled: int  # Downsampled but not transcribed
    episodes_transcribed: int  # Transcribed but not cleaned
    episodes_cleaned: int  # Cleaned but not summarized
    episodes_summarized: int  # Fully processed (summary generated)

    # Legacy fields (for backward compatibility)
    episodes_processed: int  # Same as episodes_summarized
    episodes_unprocessed: int  # Sum of all non-summarized states

    transcripts_available: int
    audio_files_count: int
    storage_path: str
    last_updated: datetime


class StatsService:
    """
    Service for retrieving system statistics and status information.

    Attributes:
        storage_path: Path to data storage directory
        repository: Repository for podcast persistence
        path_manager: Path manager for file operations
    """

    def __init__(
        self,
        storage_path: Union[str, Path],
        podcast_repository: PodcastRepository,
        path_manager: PathManager,
    ) -> None:
        """
        Initialize stats service.

        Args:
            storage_path: Path to data storage directory (str or Path)
            podcast_repository: Repository for podcast persistence
            path_manager: Path manager for file path operations
        """
        self.storage_path: Path = Path(storage_path) if isinstance(storage_path, str) else storage_path
        self.repository: PodcastRepository = podcast_repository
        self.path_manager: PathManager = path_manager
        logger.info(f"StatsService initialized with storage: {self.storage_path}")

    def get_stats(self) -> SystemStats:
        """
        Get comprehensive system statistics.

        Returns:
            SystemStats object with current system status
        """
        logger.debug("Gathering system statistics")

        # Get podcast data
        podcasts = self.repository.get_all()
        podcasts_tracked = len(podcasts)

        # Count episodes by state
        from ..models.podcast import EpisodeState

        episodes_total = 0
        episodes_discovered = 0
        episodes_downloaded = 0
        episodes_downsampled = 0
        episodes_transcribed = 0
        episodes_cleaned = 0
        episodes_summarized = 0
        transcripts_available = 0

        for podcast in podcasts:
            episodes_total += len(podcast.episodes)
            for episode in podcast.episodes:
                # Count by state
                if episode.state == EpisodeState.DISCOVERED:
                    episodes_discovered += 1
                elif episode.state == EpisodeState.DOWNLOADED:
                    episodes_downloaded += 1
                elif episode.state == EpisodeState.DOWNSAMPLED:
                    episodes_downsampled += 1
                elif episode.state == EpisodeState.TRANSCRIBED:
                    episodes_transcribed += 1
                elif episode.state == EpisodeState.CLEANED:
                    episodes_cleaned += 1
                elif episode.state == EpisodeState.SUMMARIZED:
                    episodes_summarized += 1

                # Check if summary file actually exists using PathManager
                if episode.summary_path:
                    md_path = self.path_manager.summary_file(episode.summary_path)
                    if md_path.exists():
                        transcripts_available += 1

        # Legacy fields for backward compatibility
        episodes_processed = episodes_summarized
        episodes_unprocessed = episodes_total - episodes_summarized

        # Count audio files using PathManager
        audio_path = self.path_manager.original_audio_dir()
        audio_files_count = 0
        if audio_path.exists():
            audio_files_count = len(list(audio_path.glob("*.*")))

        stats = SystemStats(
            podcasts_tracked=podcasts_tracked,
            episodes_total=episodes_total,
            episodes_discovered=episodes_discovered,
            episodes_downloaded=episodes_downloaded,
            episodes_downsampled=episodes_downsampled,
            episodes_transcribed=episodes_transcribed,
            episodes_cleaned=episodes_cleaned,
            episodes_summarized=episodes_summarized,
            episodes_processed=episodes_processed,
            episodes_unprocessed=episodes_unprocessed,
            transcripts_available=transcripts_available,
            audio_files_count=audio_files_count,
            storage_path=str(self.storage_path),
            last_updated=datetime.now(),
        )

        logger.info(
            f"Stats: {stats.podcasts_tracked} podcasts, "
            f"{stats.episodes_processed}/{stats.episodes_total} episodes processed"
        )

        return stats

    def get_recent_activity(self, limit: int = 20) -> List[ActivityItem]:
        """
        Get recent processing activity across all episodes.

        Returns episodes sorted by updated_at descending (most recent first).
        Uses updated_at as a proxy for when the episode transitioned to
        its current state.

        Args:
            limit: Maximum number of items to return (default 20)

        Returns:
            List of ActivityItem objects sorted by timestamp descending
        """
        logger.debug(f"Gathering recent activity (limit={limit})")

        podcasts = self.repository.get_all()

        # Collect all episodes with their podcast info
        activity_items: List[ActivityItem] = []
        for podcast in podcasts:
            for episode in podcast.episodes:
                activity_items.append(
                    ActivityItem(
                        episode_id=episode.id,
                        episode_title=episode.title,
                        podcast_title=podcast.title,
                        podcast_id=podcast.id,
                        state=episode.state.value,
                        timestamp=episode.updated_at,
                        pub_date=episode.pub_date,
                    )
                )

        # Sort by updated_at descending (most recent first)
        activity_items.sort(key=lambda x: x.timestamp, reverse=True)

        # Apply limit
        result = activity_items[:limit]

        logger.info(f"Found {len(activity_items)} total episodes, returning {len(result)}")
        return result
