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
Abstract repository interface for briefing persistence.

This interface defines the contract for briefing storage operations,
supporting CRUD operations and episode tracking for THES-153.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.briefing import Briefing, BriefingStatus


class BriefingRepository(ABC):
    """
    Abstract repository for briefing persistence operations.

    Implementations must provide thread-safe access to briefing data.
    """

    @abstractmethod
    def get_by_id(self, briefing_id: str) -> Optional[Briefing]:
        """
        Get briefing by internal UUID (primary key).

        Args:
            briefing_id: Internal UUID of the briefing

        Returns:
            Briefing if found, None otherwise
        """
        pass

    @abstractmethod
    def get_all(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[BriefingStatus] = None,
        user_id: Optional[str] = None,
    ) -> List[Briefing]:
        """
        Get all briefings with optional filtering.

        Args:
            limit: Maximum number of briefings to return
            offset: Number of briefings to skip
            status: Optional status filter
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of briefings ordered by created_at descending
        """
        pass

    @abstractmethod
    def get_latest(self) -> Optional[Briefing]:
        """
        Get the most recently created briefing.

        Returns:
            Most recent briefing if any exist, None otherwise
        """
        pass

    @abstractmethod
    def save(self, briefing: Briefing) -> Briefing:
        """
        Save or update a briefing.

        If a briefing with the same ID exists, it will be updated.
        Otherwise, a new briefing will be created.

        Args:
            briefing: Briefing to save or update

        Returns:
            The saved briefing
        """
        pass

    @abstractmethod
    def delete(self, briefing_id: str) -> bool:
        """
        Delete briefing by ID.

        Args:
            briefing_id: Internal UUID of the briefing to delete

        Returns:
            True if briefing was deleted, False if not found
        """
        pass

    @abstractmethod
    def get_episodes_in_briefing(self, briefing_id: str) -> List[str]:
        """
        Get list of episode IDs included in a briefing.

        Args:
            briefing_id: Internal UUID of the briefing

        Returns:
            List of episode UUIDs included in the briefing
        """
        pass

    @abstractmethod
    def is_episode_in_any_briefing(self, episode_id: str) -> bool:
        """
        Check if an episode has been included in any briefing.

        Useful for --ready-only mode to prevent duplicate inclusions.

        Args:
            episode_id: Internal UUID of the episode

        Returns:
            True if episode is in at least one briefing
        """
        pass

    @abstractmethod
    def get_briefings_containing_episode(self, episode_id: str, user_id: Optional[str] = None) -> List[Briefing]:
        """
        Get all briefings that contain a specific episode.

        Args:
            episode_id: Internal UUID of the episode
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of briefings containing the episode
        """
        pass

    @abstractmethod
    def get_briefings_in_period(
        self,
        start: datetime,
        end: datetime,
        user_id: Optional[str] = None,
    ) -> List[Briefing]:
        """
        Get briefings whose period overlaps with the given time range.

        Args:
            start: Start of the time range
            end: End of the time range
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of briefings with overlapping periods
        """
        pass

    @abstractmethod
    def count(
        self,
        status: Optional[BriefingStatus] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """
        Count briefings with optional filtering.

        More efficient than get_all() when only the count is needed.

        Args:
            status: Optional status filter
            user_id: Optional user ID filter (None for all users)

        Returns:
            Total count of matching briefings
        """
        pass
