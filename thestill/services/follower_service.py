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
Follower service for managing user-podcast follow relationships.

Handles the business logic for following and unfollowing podcasts,
supporting the multi-user shared podcasts architecture.
"""

import sqlite3
from typing import List, Optional

from structlog import get_logger

from ..models.podcast import Podcast
from ..models.user import PodcastFollower
from ..repositories.podcast_follower_repository import PodcastFollowerRepository
from ..repositories.podcast_repository import PodcastRepository

logger = get_logger(__name__)


class FollowerServiceError(Exception):
    """Base exception for follower service errors."""

    pass


class AlreadyFollowingError(FollowerServiceError):
    """Raised when user already follows the podcast."""

    pass


class NotFollowingError(FollowerServiceError):
    """Raised when user does not follow the podcast."""

    pass


class PodcastNotFoundError(FollowerServiceError):
    """Raised when podcast does not exist."""

    pass


class FollowerService:
    """
    Service for managing user-podcast follow relationships.

    Handles business logic and validation for follow/unfollow operations.
    """

    def __init__(
        self,
        follower_repository: PodcastFollowerRepository,
        podcast_repository: PodcastRepository,
    ):
        """
        Initialize the follower service.

        Args:
            follower_repository: Repository for follower persistence
            podcast_repository: Repository for podcast data (for validation)
        """
        self.follower_repository = follower_repository
        self.podcast_repository = podcast_repository

        logger.info("FollowerService initialized")

    def follow(self, user_id: str, podcast_id: str) -> PodcastFollower:
        """
        User follows a podcast.

        Args:
            user_id: ID of the user
            podcast_id: ID of the podcast to follow

        Returns:
            The created PodcastFollower relationship

        Raises:
            PodcastNotFoundError: If podcast doesn't exist
            AlreadyFollowingError: If user already follows the podcast
        """
        # Validate podcast exists
        podcast = self.podcast_repository.get_by_id(podcast_id)
        if not podcast:
            raise PodcastNotFoundError(f"Podcast not found: {podcast_id}")

        # Check if already following
        if self.follower_repository.exists(user_id, podcast_id):
            raise AlreadyFollowingError(f"User {user_id} already follows podcast {podcast_id}")

        # Create follower relationship
        follower = PodcastFollower(user_id=user_id, podcast_id=podcast_id)

        try:
            saved = self.follower_repository.add(follower)
            logger.info(f"User {user_id} followed podcast {podcast_id}")
            return saved
        except sqlite3.IntegrityError as e:
            # Handle race condition where another request created the relationship
            raise AlreadyFollowingError(f"User {user_id} already follows podcast {podcast_id}") from e

    def unfollow(self, user_id: str, podcast_id: str) -> bool:
        """
        User unfollows a podcast.

        Args:
            user_id: ID of the user
            podcast_id: ID of the podcast to unfollow

        Returns:
            True if unfollowed successfully

        Raises:
            NotFollowingError: If user doesn't follow the podcast
        """
        # Remove the relationship
        removed = self.follower_repository.remove(user_id, podcast_id)

        if not removed:
            raise NotFollowingError(f"User {user_id} does not follow podcast {podcast_id}")

        logger.info(f"User {user_id} unfollowed podcast {podcast_id}")
        return True

    def is_following(self, user_id: str, podcast_id: str) -> bool:
        """
        Check if user follows a podcast.

        Args:
            user_id: ID of the user
            podcast_id: ID of the podcast

        Returns:
            True if user follows the podcast
        """
        return self.follower_repository.exists(user_id, podcast_id)

    def get_followed_podcasts(self, user_id: str) -> List[Podcast]:
        """
        Get all podcasts a user follows.

        Args:
            user_id: ID of the user

        Returns:
            List of Podcast objects the user follows
        """
        # Get followed podcast IDs
        podcast_ids = self.follower_repository.get_followed_podcast_ids(user_id)

        if not podcast_ids:
            return []

        # Fetch podcast details
        podcasts = []
        for podcast_id in podcast_ids:
            podcast = self.podcast_repository.get_by_id(podcast_id)
            if podcast:
                podcasts.append(podcast)

        return podcasts

    def get_follower_count(self, podcast_id: str) -> int:
        """
        Get the number of followers for a podcast.

        Args:
            podcast_id: ID of the podcast

        Returns:
            Number of users following the podcast
        """
        return self.follower_repository.count_by_podcast(podcast_id)

    def follow_by_slug(self, user_id: str, podcast_slug: str) -> PodcastFollower:
        """
        User follows a podcast by its slug.

        Args:
            user_id: ID of the user
            podcast_slug: Slug of the podcast to follow

        Returns:
            The created PodcastFollower relationship

        Raises:
            PodcastNotFoundError: If podcast doesn't exist
            AlreadyFollowingError: If user already follows the podcast
        """
        podcast = self.podcast_repository.get_by_slug(podcast_slug)
        if not podcast:
            raise PodcastNotFoundError(f"Podcast not found: {podcast_slug}")

        return self.follow(user_id, podcast.id)

    def unfollow_by_slug(self, user_id: str, podcast_slug: str) -> bool:
        """
        User unfollows a podcast by its slug.

        Args:
            user_id: ID of the user
            podcast_slug: Slug of the podcast to unfollow

        Returns:
            True if unfollowed successfully

        Raises:
            PodcastNotFoundError: If podcast doesn't exist
            NotFollowingError: If user doesn't follow the podcast
        """
        podcast = self.podcast_repository.get_by_slug(podcast_slug)
        if not podcast:
            raise PodcastNotFoundError(f"Podcast not found: {podcast_slug}")

        return self.unfollow(user_id, podcast.id)

    def is_following_by_slug(self, user_id: str, podcast_slug: str) -> bool:
        """
        Check if user follows a podcast by slug.

        Args:
            user_id: ID of the user
            podcast_slug: Slug of the podcast

        Returns:
            True if user follows the podcast, False otherwise (including if podcast not found)
        """
        podcast = self.podcast_repository.get_by_slug(podcast_slug)
        if not podcast:
            return False

        return self.is_following(user_id, podcast.id)

    def get_follower_count_by_slug(self, podcast_slug: str) -> Optional[int]:
        """
        Get the number of followers for a podcast by slug.

        Args:
            podcast_slug: Slug of the podcast

        Returns:
            Number of followers, or None if podcast not found
        """
        podcast = self.podcast_repository.get_by_slug(podcast_slug)
        if not podcast:
            return None

        return self.get_follower_count(podcast.id)
