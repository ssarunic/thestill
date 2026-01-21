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
Abstract repository interface for podcast follower persistence.

This interface defines the contract for managing user-podcast follow relationships,
supporting the multi-user shared podcasts architecture.
"""

from abc import ABC, abstractmethod
from typing import List

from ..models.user import PodcastFollower


class PodcastFollowerRepository(ABC):
    """
    Abstract repository for podcast follower persistence operations.

    Manages the many-to-many relationship between users and podcasts.
    Implementations must provide thread-safe access.
    """

    @abstractmethod
    def add(self, follower: PodcastFollower) -> PodcastFollower:
        """
        Add a follower relationship.

        Args:
            follower: PodcastFollower to add

        Returns:
            The saved PodcastFollower

        Raises:
            sqlite3.IntegrityError: If relationship already exists
        """
        pass

    @abstractmethod
    def remove(self, user_id: str, podcast_id: str) -> bool:
        """
        Remove a follower relationship.

        Args:
            user_id: ID of the user
            podcast_id: ID of the podcast

        Returns:
            True if relationship was removed, False if not found
        """
        pass

    @abstractmethod
    def exists(self, user_id: str, podcast_id: str) -> bool:
        """
        Check if a follower relationship exists.

        Args:
            user_id: ID of the user
            podcast_id: ID of the podcast

        Returns:
            True if user follows podcast, False otherwise
        """
        pass

    @abstractmethod
    def get_by_user(self, user_id: str) -> List[PodcastFollower]:
        """
        Get all podcasts followed by a user.

        Args:
            user_id: ID of the user

        Returns:
            List of PodcastFollower relationships
        """
        pass

    @abstractmethod
    def get_by_podcast(self, podcast_id: str) -> List[PodcastFollower]:
        """
        Get all followers of a podcast.

        Args:
            podcast_id: ID of the podcast

        Returns:
            List of PodcastFollower relationships
        """
        pass

    @abstractmethod
    def count_by_podcast(self, podcast_id: str) -> int:
        """
        Count followers for a podcast.

        Args:
            podcast_id: ID of the podcast

        Returns:
            Number of followers
        """
        pass

    @abstractmethod
    def get_followed_podcast_ids(self, user_id: str) -> List[str]:
        """
        Get IDs of podcasts a user follows.

        Optimized for filtering queries.

        Args:
            user_id: ID of the user

        Returns:
            List of podcast IDs
        """
        pass
