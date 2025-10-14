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
Stats service - System statistics and status information
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Union

from pydantic import BaseModel

from ..repositories.podcast_repository import PodcastRepository
from ..utils.path_manager import PathManager

logger = logging.getLogger(__name__)


class SystemStats(BaseModel):
    """System-wide statistics"""

    podcasts_tracked: int
    episodes_total: int

    # Episode counts by state (pipeline progression)
    episodes_discovered: int  # Not yet downloaded
    episodes_downloaded: int  # Downloaded but not downsampled
    episodes_downsampled: int  # Downsampled but not transcribed
    episodes_transcribed: int  # Transcribed but not cleaned
    episodes_cleaned: int  # Fully processed

    # Legacy fields (for backward compatibility)
    episodes_processed: int  # Same as episodes_cleaned
    episodes_unprocessed: int  # Sum of all non-cleaned states

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
        podcasts = self.repository.find_all()
        podcasts_tracked = len(podcasts)

        # Count episodes by state
        from ..models.podcast import EpisodeState

        episodes_total = 0
        episodes_discovered = 0
        episodes_downloaded = 0
        episodes_downsampled = 0
        episodes_transcribed = 0
        episodes_cleaned = 0
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

                # Check if cleaned transcript file actually exists using PathManager
                if episode.summary_path:
                    md_path = self.path_manager.summary_file(episode.summary_path)
                    if md_path.with_suffix(".md").exists():
                        transcripts_available += 1

        # Legacy fields for backward compatibility
        episodes_processed = episodes_cleaned
        episodes_unprocessed = episodes_total - episodes_cleaned

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
