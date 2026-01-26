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
External transcript downloader for Podcasting 2.0 <podcast:transcript> tags.

Downloads external transcripts from RSS feeds in all available formats
(SRT, VTT, JSON, HTML, plain text) for evaluation and debugging purposes.
These transcripts are stored separately from locally-generated transcripts.
"""

from typing import Dict, List, Optional

import requests
from structlog import get_logger

from ..models.podcast import TranscriptLink
from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
from ..utils.path_manager import PathManager

logger = get_logger(__name__)

# Timeout for transcript downloads (seconds)
DOWNLOAD_TIMEOUT = 30


class ExternalTranscriptDownloader:
    """
    Downloads external transcripts from RSS feed URLs.

    Handles:
    - Downloading all available transcript formats for an episode
    - Saving to external_transcripts/{podcast_slug}/{episode_slug}.{ext}
    - Updating download status in database
    - Graceful error handling (continue on failure)
    """

    def __init__(self, repository: SqlitePodcastRepository, path_manager: PathManager):
        """
        Initialize downloader.

        Args:
            repository: Database repository for tracking download status
            path_manager: Path manager for determining storage paths
        """
        self.repository = repository
        self.path_manager = path_manager

    def download_all_for_episode(
        self,
        episode_id: str,
        podcast_slug: str,
        episode_slug: str,
    ) -> Dict[str, str]:
        """
        Download all available transcript formats for an episode.

        Args:
            episode_id: Episode UUID
            podcast_slug: Slugified podcast title
            episode_slug: Slugified episode title

        Returns:
            Dict mapping format extension -> local file path for successful downloads
        """
        # Get transcript links from database
        links = self.repository.get_transcript_links(episode_id)
        if not links:
            logger.debug(f"No transcript links found for episode {episode_id}")
            return {}

        # Filter to undownloaded links only
        pending_links = [link for link in links if not link.downloaded_path]
        if not pending_links:
            logger.debug(f"All transcript links already downloaded for episode {episode_id}")
            return {}

        logger.info(f"Downloading {len(pending_links)} transcript format(s) for {episode_slug}")

        # Ensure directory exists
        podcast_dir = self.path_manager.external_transcript_dir_for_podcast(podcast_slug)
        podcast_dir.mkdir(parents=True, exist_ok=True)

        downloaded: Dict[str, str] = {}

        for link in pending_links:
            try:
                local_path = self._download_transcript(link, podcast_slug, episode_slug)
                if local_path:
                    downloaded[link.format_extension] = local_path

                    # Update database
                    if link.id:
                        self.repository.mark_transcript_downloaded(link.id, local_path)

            except Exception as e:
                logger.warning(f"Failed to download {link.mime_type} transcript from {link.url}: {e}")
                continue

        if downloaded:
            logger.info(f"Downloaded {len(downloaded)} transcript(s) for {episode_slug}: {list(downloaded.keys())}")

        return downloaded

    def _download_transcript(
        self,
        link: TranscriptLink,
        podcast_slug: str,
        episode_slug: str,
    ) -> Optional[str]:
        """
        Download a single transcript file.

        Args:
            link: TranscriptLink with URL and type info
            podcast_slug: Slugified podcast title
            episode_slug: Slugified episode title

        Returns:
            Local file path if successful, None otherwise
        """
        # Determine file path
        extension = link.format_extension
        file_path = self.path_manager.external_transcript_file(podcast_slug, episode_slug, extension)

        # Skip if file already exists
        if file_path.exists():
            logger.debug(f"Transcript file already exists: {file_path}")
            return str(file_path)

        # Download
        logger.debug(f"Downloading {extension} transcript from {link.url}")

        response = requests.get(
            str(link.url),
            timeout=DOWNLOAD_TIMEOUT,
            headers={"User-Agent": "thestill.me podcast transcription pipeline"},
        )
        response.raise_for_status()

        # Save to file
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(response.content)

        logger.debug(f"Saved transcript to {file_path}")
        return str(file_path)

    def download_all_pending(
        self,
        podcast_id: Optional[str] = None,
        max_episodes: Optional[int] = None,
    ) -> int:
        """
        Download transcripts for all episodes with pending transcript links.

        Args:
            podcast_id: Optional podcast UUID to filter by
            max_episodes: Optional limit on number of episodes to process

        Returns:
            Number of episodes processed
        """
        # Get episodes with undownloaded transcript links
        episodes_with_links = self.repository.get_episodes_with_undownloaded_transcript_links(podcast_id)

        if not episodes_with_links:
            logger.info("No episodes with pending transcript downloads")
            return 0

        # Apply limit if set
        if max_episodes:
            episodes_with_links = episodes_with_links[:max_episodes]

        logger.info(f"Downloading transcripts for {len(episodes_with_links)} episode(s)")

        processed = 0
        for episode, links in episodes_with_links:
            # We need the podcast slug - get it from the first link's path if already downloaded
            # or query it separately
            # For now, we'll need to get the podcast info
            podcast = self.repository.get_podcast_for_episode(episode.id)
            if not podcast:
                logger.warning(f"Could not find podcast for episode {episode.id}")
                continue

            self.download_all_for_episode(
                episode_id=episode.id,
                podcast_slug=podcast.slug,
                episode_slug=episode.slug,
            )
            processed += 1

        return processed
