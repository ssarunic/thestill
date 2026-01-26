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
Abstract repository interface for digest persistence.

This interface defines the contract for digest storage operations,
supporting CRUD operations and episode tracking for THES-153.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.digest import Digest, DigestStatus


class DigestRepository(ABC):
    """
    Abstract repository for digest persistence operations.

    Implementations must provide thread-safe access to digest data.
    """

    @abstractmethod
    def get_by_id(self, digest_id: str) -> Optional[Digest]:
        """
        Get digest by internal UUID (primary key).

        Args:
            digest_id: Internal UUID of the digest

        Returns:
            Digest if found, None otherwise
        """
        pass

    @abstractmethod
    def get_all(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[DigestStatus] = None,
        user_id: Optional[str] = None,
    ) -> List[Digest]:
        """
        Get all digests with optional filtering.

        Args:
            limit: Maximum number of digests to return
            offset: Number of digests to skip
            status: Optional status filter
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of digests ordered by created_at descending
        """
        pass

    @abstractmethod
    def get_latest(self) -> Optional[Digest]:
        """
        Get the most recently created digest.

        Returns:
            Most recent digest if any exist, None otherwise
        """
        pass

    @abstractmethod
    def save(self, digest: Digest) -> Digest:
        """
        Save or update a digest.

        If a digest with the same ID exists, it will be updated.
        Otherwise, a new digest will be created.

        Args:
            digest: Digest to save or update

        Returns:
            The saved digest
        """
        pass

    @abstractmethod
    def delete(self, digest_id: str) -> bool:
        """
        Delete digest by ID.

        Args:
            digest_id: Internal UUID of the digest to delete

        Returns:
            True if digest was deleted, False if not found
        """
        pass

    @abstractmethod
    def get_episodes_in_digest(self, digest_id: str) -> List[str]:
        """
        Get list of episode IDs included in a digest.

        Args:
            digest_id: Internal UUID of the digest

        Returns:
            List of episode UUIDs included in the digest
        """
        pass

    @abstractmethod
    def is_episode_in_any_digest(self, episode_id: str) -> bool:
        """
        Check if an episode has been included in any digest.

        Useful for --ready-only mode to prevent duplicate inclusions.

        Args:
            episode_id: Internal UUID of the episode

        Returns:
            True if episode is in at least one digest
        """
        pass

    @abstractmethod
    def get_digests_containing_episode(self, episode_id: str, user_id: Optional[str] = None) -> List[Digest]:
        """
        Get all digests that contain a specific episode.

        Args:
            episode_id: Internal UUID of the episode
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of digests containing the episode
        """
        pass

    @abstractmethod
    def get_digests_in_period(
        self,
        start: datetime,
        end: datetime,
        user_id: Optional[str] = None,
    ) -> List[Digest]:
        """
        Get digests whose period overlaps with the given time range.

        Args:
            start: Start of the time range
            end: End of the time range
            user_id: Optional user ID filter (None for all users)

        Returns:
            List of digests with overlapping periods
        """
        pass
