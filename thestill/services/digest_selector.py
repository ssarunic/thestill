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
Episode selector for digest processing.

Implements THES-25: Episode selector with safety limits for the digest command.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from structlog import get_logger

from ..models.podcast import Episode, EpisodeState, Podcast
from ..repositories.digest_repository import DigestRepository
from ..repositories.podcast_repository import EpisodeRepository

logger = get_logger(__name__)


@dataclass
class DigestSelectionCriteria:
    """
    Criteria for selecting episodes for digest processing.

    Attributes:
        since_days: Only include episodes published within this many days (default: 7)
        max_episodes: Maximum number of episodes to return (default: 10)
        podcast_id: Filter to specific podcast by UUID (optional)
        ready_only: If True, only select SUMMARIZED episodes (skip pipeline processing)
        exclude_digested: If True, exclude episodes already included in a digest
    """

    since_days: int = 7
    max_episodes: int = 10
    podcast_id: Optional[str] = None
    ready_only: bool = False
    exclude_digested: bool = False

    @property
    def date_from(self) -> datetime:
        """Calculate the cutoff date based on since_days."""
        return datetime.now(timezone.utc) - timedelta(days=self.since_days)


@dataclass
class DigestSelectionResult:
    """
    Result of episode selection.

    Attributes:
        episodes: List of (Podcast, Episode) tuples selected for processing
        total_matching: Total count of episodes matching criteria (before limit applied)
        criteria: The criteria used for selection
    """

    episodes: List[Tuple[Podcast, Episode]]
    total_matching: int
    criteria: DigestSelectionCriteria


class DigestEpisodeSelector:
    """
    Selects episodes for digest processing with safety limits.

    This class implements the episode selection logic for the digest command,
    applying safety limits to prevent accidentally processing too many episodes.

    Safety features:
    - Default time window of 7 days (DIGEST_DEFAULT_SINCE_DAYS)
    - Default max episodes of 10 (DIGEST_DEFAULT_MAX_EPISODES)
    - Only selects episodes that need processing (excludes SUMMARIZED and FAILED)

    Ready-only mode (--ready-only):
    - Only selects SUMMARIZED episodes for digest generation
    - Skips pipeline processing entirely
    - Can exclude already-digested episodes with exclude_digested flag
    """

    def __init__(
        self,
        episode_repository: EpisodeRepository,
        digest_repository: Optional[DigestRepository] = None,
    ):
        """
        Initialize digest episode selector.

        Args:
            episode_repository: Repository for episode queries
            digest_repository: Repository for digest queries (optional, needed for exclude_digested)
        """
        self.repository = episode_repository
        self.digest_repository = digest_repository

    def select(self, criteria: DigestSelectionCriteria) -> DigestSelectionResult:
        """
        Select episodes for digest processing based on criteria.

        Normal mode (ready_only=False):
        - Selects episodes that need processing (excludes SUMMARIZED and FAILED states)

        Ready-only mode (ready_only=True):
        - Selects only SUMMARIZED episodes for immediate digest generation
        - Skips pipeline processing entirely

        Episodes are selected if they:
        1. Were published within the specified time window (since_days)
        2. Match the state filter based on ready_only flag
        3. Match the podcast_id filter if specified
        4. Are not already in a digest (if exclude_digested=True)

        Results are ordered by publish date (newest first) and limited by max_episodes.

        Args:
            criteria: Selection criteria including time window, limits, and filters

        Returns:
            DigestSelectionResult containing matched episodes and metadata
        """
        logger.info(
            "Selecting episodes for digest",
            since_days=criteria.since_days,
            max_episodes=criteria.max_episodes,
            podcast_id=criteria.podcast_id,
            ready_only=criteria.ready_only,
            exclude_digested=criteria.exclude_digested,
        )

        # Query episodes without state filter - we'll filter in Python
        # Using a large limit to get all candidates, then apply our own limit
        #
        # For ready_only mode (morning briefing), filter by updated_at (when episode was
        # summarized) instead of pub_date (when episode was published). This ensures we
        # find episodes that were recently processed, not just recently published.
        if criteria.ready_only:
            episodes, total = self.repository.get_all_episodes(
                limit=1000,
                offset=0,
                podcast_id=criteria.podcast_id,
                updated_from=criteria.date_from,  # Filter by summarization date
                sort_by="updated_at",
                sort_order="desc",
            )
        else:
            episodes, total = self.repository.get_all_episodes(
                limit=1000,
                offset=0,
                podcast_id=criteria.podcast_id,
                date_from=criteria.date_from,  # Filter by publication date
                sort_by="pub_date",
                sort_order="desc",
            )

        if criteria.ready_only:
            # Ready-only mode: only select SUMMARIZED episodes
            filtered = [(podcast, episode) for podcast, episode in episodes if episode.state == EpisodeState.SUMMARIZED]
        else:
            # Normal mode: exclude SUMMARIZED and FAILED - everything else needs processing
            filtered = [
                (podcast, episode)
                for podcast, episode in episodes
                if episode.state not in (EpisodeState.SUMMARIZED, EpisodeState.FAILED)
            ]

        # Optionally exclude episodes already in a digest
        if criteria.exclude_digested and self.digest_repository:
            filtered = [
                (podcast, episode)
                for podcast, episode in filtered
                if not self.digest_repository.is_episode_in_any_digest(episode.id)
            ]

        total_matching = len(filtered)

        # Apply max_episodes limit
        selected = filtered[: criteria.max_episodes]

        logger.info(
            "Episode selection complete",
            total_matching=total_matching,
            selected_count=len(selected),
            max_episodes=criteria.max_episodes,
            ready_only=criteria.ready_only,
        )

        return DigestSelectionResult(
            episodes=selected,
            total_matching=total_matching,
            criteria=criteria,
        )

    def preview(self, criteria: DigestSelectionCriteria) -> DigestSelectionResult:
        """
        Preview episode selection without committing to processing.

        This is identical to select() but is semantically a preview operation
        for use with --dry-run flag.

        Args:
            criteria: Selection criteria

        Returns:
            DigestSelectionResult with preview of what would be selected
        """
        return self.select(criteria)
