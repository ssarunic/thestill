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
Refresh service - Business logic for feed refreshing and episode discovery
"""

import logging
from typing import List, Optional, Tuple, Union

from pydantic import BaseModel

from ..core.feed_manager import PodcastFeedManager
from ..models.podcast import Episode, Podcast
from .podcast_service import PodcastService

logger = logging.getLogger(__name__)


class RefreshResult(BaseModel):
    """Result of a refresh operation"""

    total_episodes: int
    episodes_by_podcast: List[Tuple[Podcast, List[Episode]]]
    podcast_filter_applied: Optional[str] = None


class RefreshService:
    """
    Service for refreshing podcast feeds and discovering new episodes.

    Handles the business logic of:
    - Fetching new episodes from RSS feeds
    - Filtering by podcast ID
    - Applying episode limits
    - Managing dry-run mode
    """

    def __init__(self, feed_manager: PodcastFeedManager, podcast_service: PodcastService):
        """
        Initialize refresh service.

        Args:
            feed_manager: Feed manager for RSS operations
            podcast_service: Podcast service for podcast lookups
        """
        self.feed_manager = feed_manager
        self.podcast_service = podcast_service

    def refresh(
        self,
        podcast_id: Optional[Union[str, int]] = None,
        max_episodes: Optional[int] = None,
        max_episodes_per_podcast: Optional[int] = None,
        dry_run: bool = False,
    ) -> RefreshResult:
        """
        Refresh podcast feeds and discover new episodes.

        Args:
            podcast_id: Optional podcast ID or RSS URL to filter
            max_episodes: Maximum episodes to process (applied after filtering)
            max_episodes_per_podcast: Maximum episodes to discover per podcast
            dry_run: If True, don't persist changes

        Returns:
            RefreshResult with discovered episodes

        Raises:
            ValueError: If podcast_id is specified but not found
        """
        logger.info("Starting feed refresh...")

        # Get new episodes from all podcasts
        new_episodes = self.feed_manager.get_new_episodes(max_episodes_per_podcast=max_episodes_per_podcast)

        if not new_episodes:
            logger.info("No new episodes found")
            return RefreshResult(total_episodes=0, episodes_by_podcast=[])

        # Filter by podcast_id if specified
        podcast_filter_name = None
        if podcast_id:
            podcast = self.podcast_service.get_podcast(podcast_id)
            if not podcast:
                raise ValueError(f"Podcast not found: {podcast_id}")

            # Filter new_episodes to only include the specified podcast
            new_episodes = [(p, eps) for p, eps in new_episodes if str(p.rss_url) == str(podcast.rss_url)]
            podcast_filter_name = podcast.title

            if not new_episodes:
                logger.info(f"No new episodes found for podcast: {podcast.title}")
                return RefreshResult(
                    total_episodes=0, episodes_by_podcast=[], podcast_filter_applied=podcast_filter_name
                )

        # Apply max_episodes limit per podcast
        episodes_to_add = []
        for podcast, episodes in new_episodes:
            if max_episodes:
                episodes = episodes[:max_episodes]
            episodes_to_add.append((podcast, episodes))

        # Count total episodes
        total_episodes = sum(len(eps) for _, eps in episodes_to_add)

        # Persist changes if not dry-run
        if not dry_run:
            self.feed_manager._save_podcasts()
            logger.info(f"Refresh complete! Discovered {total_episodes} new episode(s)")

        return RefreshResult(
            total_episodes=total_episodes,
            episodes_by_podcast=episodes_to_add,
            podcast_filter_applied=podcast_filter_name,
        )
