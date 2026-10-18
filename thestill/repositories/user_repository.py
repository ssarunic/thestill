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
Abstract repository interface for user persistence.

This interface defines the contract for user storage operations,
supporting both single-user and multi-user authentication modes.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models.user import User, UserStatus


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
    def update_usage_analytics(self, user_id: str, enabled: bool) -> bool:
        """Set the user's usage-events switch (spec #95). Returns True if a
        row was updated.

        Turning it off also erases, in the same transaction, what was
        recorded only to measure (spec #103): the user's
        ``user_episode_views`` rows and ``user_episode_progress.listened_at``.
        Positions and completions stay; the service needs them."""

    @abstractmethod
    def update_region(self, user_id: str, region: Optional[str], locked: bool) -> bool:
        """
        Update the user's region and lock flag.

        Args:
            user_id: Internal UUID of the user
            region: ISO 3166-1 alpha-2 country code (lowercase) or None to clear
            locked: True if this is an explicit user choice (suppresses future
                IP-based inference); False if it's an inferred guess

        Returns:
            True if user was found and updated, False otherwise
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

    # ----- Spec #104: account status and admin flag -----

    @abstractmethod
    def apply_status(
        self,
        user_id: str,
        status: UserStatus,
        *,
        reason: Optional[str],
        changed_by: Optional[str],
        changed_at: datetime,
        tokens_valid_after: Optional[datetime] = None,
    ) -> bool:
        """Set the account status with the operator's reason, in one UPDATE.

        ``tokens_valid_after``, when given, also revokes every JWT issued
        before it (suspend and ban do; reinstate leaves it). Returns True
        if a row was updated."""

    @abstractmethod
    def set_tokens_valid_after(self, user_id: str, when: datetime) -> bool:
        """Reject every JWT issued before ``when`` ("sign out everywhere")."""

    @abstractmethod
    def set_admin(self, user_id: str, is_admin: bool) -> bool:
        """Grant or revoke admin. Returns True if a row was updated."""

    # ----- JWT revocation deny-list (spec #25 item 4.2) -----

    @abstractmethod
    def revoke_token(self, jti: str, expires_at: datetime) -> None:
        """Add a token's ``jti`` to the revocation deny-list.

        ``expires_at`` is the token's original ``exp`` so the row can
        be pruned once the token would have expired anyway. Idempotent
        — re-revoking the same jti is a no-op.
        """

    @abstractmethod
    def is_token_revoked(self, jti: str) -> bool:
        """Return True if ``jti`` is on the revocation deny-list."""

    @abstractmethod
    def prune_expired_revocations(self) -> int:
        """Drop revoked-token rows whose ``expires_at`` has passed.

        Returns the number of rows deleted. Safe to call lazily — a
        revoked-then-expired token is rejected by the signature check
        anyway, so the row's only purpose was to plug the window
        between revocation and expiry.
        """
