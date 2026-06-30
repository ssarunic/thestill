# Copyright 2025-2026 Thestill
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

from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple, Union

from pydantic import BaseModel
from structlog import get_logger

from ..core.feed_manager import PodcastFeedManager
from ..models.podcast import Episode, Podcast
from .podcast_service import PodcastService

if TYPE_CHECKING:
    from ..core.queue_manager import QueueManager

logger = get_logger(__name__)


class RefreshResult(BaseModel):
    """Result of a refresh operation"""

    total_episodes: int
    episodes_by_podcast: List[Tuple[Podcast, List[Episode]]]
    podcast_filter_applied: Optional[str] = None
    # Number of feeds that errored during this refresh (spec #42, FM-4).
    # Surfaced to the CLI so a silent-fleet event is visible (and exits
    # non-zero) instead of looking like a clean "0 new episodes".
    podcasts_with_errors: int = 0


class RefreshService:
    """
    Service for refreshing podcast feeds and discovering new episodes.

    Handles the business logic of:
    - Fetching new episodes from RSS feeds
    - Filtering by podcast ID
    - Applying episode limits
    - Managing dry-run mode

    Attributes:
        feed_manager: Feed manager for RSS operations
        podcast_service: Podcast service for podcast lookups
    """

    def __init__(
        self,
        feed_manager: PodcastFeedManager,
        podcast_service: PodcastService,
        queue_manager: Optional["QueueManager"] = None,
        config: Any = None,
    ) -> None:
        """
        Initialize refresh service.

        Args:
            feed_manager: Feed manager for RSS operations
            podcast_service: Podcast service for podcast lookups
            queue_manager: Optional task queue. When provided (with ``config``),
                a non-dry-run refresh auto-enqueues newly discovered episodes for
                the full pipeline — matching the queued ``handle_refresh_feed``
                path. Omitted (``None``) in contexts without a worker queue, in
                which case refresh only discovers + persists (legacy behaviour).
            config: App config, required alongside ``queue_manager`` for the
                provider-aware entry-stage choice and the backfill cap.
        """
        self.feed_manager: PodcastFeedManager = feed_manager
        self.podcast_service: PodcastService = podcast_service
        self.queue_manager: Optional["QueueManager"] = queue_manager
        self.config: Any = config

    def refresh(
        self,
        podcast_id: Optional[Union[str, int]] = None,
        max_episodes: Optional[int] = None,
        max_episodes_per_podcast: Optional[int] = None,
        dry_run: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> RefreshResult:
        """
        Refresh podcast feeds and discover new episodes.

        Args:
            podcast_id: Optional podcast ID or RSS URL to filter
            max_episodes: Maximum episodes to process (applied after filtering)
            max_episodes_per_podcast: Maximum episodes to discover per podcast
            dry_run: If True, don't persist changes
            progress_callback: Optional callback for progress reporting.
                              Called with (current_index, total_count, podcast_title).

        Returns:
            RefreshResult with discovered episodes

        Raises:
            ValueError: If podcast_id is specified but not found
        """
        logger.info("Starting feed refresh...")

        # Get new episodes - pass podcast_id to filter BEFORE fetching feeds
        # This avoids fetching all podcasts when only one is needed.
        # ``refresh_feeds`` (vs ``get_new_episodes``) also returns the batch
        # error count so we can surface a silent-fleet event (FM-4).
        outcome = self.feed_manager.refresh_feeds(
            max_episodes_per_podcast=max_episodes_per_podcast,
            progress_callback=progress_callback,
            podcast_id=str(podcast_id) if podcast_id else None,
        )
        new_episodes = outcome.episodes_by_podcast

        # Determine podcast filter name for response
        podcast_filter_name = None
        if podcast_id:
            podcast = self.podcast_service.get_podcast(podcast_id)
            if podcast:
                podcast_filter_name = podcast.title

        if not new_episodes:
            logger.info("No new episodes found")
            return RefreshResult(
                total_episodes=0,
                episodes_by_podcast=[],
                podcast_filter_applied=podcast_filter_name,
                podcasts_with_errors=outcome.podcasts_with_errors,
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
            # Changes are already persisted by feed_manager.refresh_feeds().
            # Auto-enqueue newly discovered episodes for the full pipeline so an
            # inline refresh (CLI / web / MCP) processes new episodes the same
            # way the queued ``handle_refresh_feed`` path does. Drives off DB
            # state per refreshed podcast, so it's idempotent and skips episodes
            # already queued. Gated on a wired queue_manager + config; absent
            # those, refresh stays discover-and-persist only.
            enqueued_total = 0
            if self.queue_manager is not None and self.config is not None:
                for podcast, episodes in episodes_to_add:
                    if not episodes:
                        continue
                    enqueued_total += self.queue_manager.enqueue_discovered_episodes(
                        podcast_id=podcast.id,
                        repository=self.feed_manager.repository,
                        config=self.config,
                        initiated_by="refresh",
                    )
            logger.info("Refresh complete", total_episodes=total_episodes, tasks_enqueued=enqueued_total)

        return RefreshResult(
            total_episodes=total_episodes,
            episodes_by_podcast=episodes_to_add,
            podcast_filter_applied=podcast_filter_name,
            podcasts_with_errors=outcome.podcasts_with_errors,
        )
