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
Abstract repository interface for user persistence.

This interface defines the contract for user storage operations,
supporting both single-user and multi-user authentication modes.
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models.user import User


class UserRepository(ABC):
    """
    Abstract repository for user persistence operations.

    Implementations must provide thread-safe access to user data.
    """

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[User]:
        """
        Get user by internal UUID (primary key).

        Args:
            user_id: Internal UUID of the user

        Returns:
            User if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email address.

        Args:
            email: User's email address

        Returns:
            User if found, None otherwise
        """
        pass

    @abstractmethod
    def get_by_google_id(self, google_id: str) -> Optional[User]:
        """
        Get user by Google's unique user ID.

        Args:
            google_id: Google's unique identifier for the user

        Returns:
            User if found, None otherwise
        """
        pass

    @abstractmethod
    def save(self, user: User) -> User:
        """
        Save or update a user.

        If a user with the same email already exists, it will be updated.
        Otherwise, a new user will be created.

        Args:
            user: User to save or update

        Returns:
            The saved user (may include updated timestamps)
        """
        pass

    @abstractmethod
    def update_last_login(self, user_id: str) -> bool:
        """
        Update the last_login_at timestamp for a user.

        Args:
            user_id: Internal UUID of the user

        Returns:
            True if user was found and updated, False otherwise
        """
        pass

    @abstractmethod
    def delete(self, user_id: str) -> bool:
        """
        Delete user by ID.

        Args:
            user_id: Internal UUID of the user to delete

        Returns:
            True if user was deleted, False if not found
        """
        pass
