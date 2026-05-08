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
Per-user inbox service.

Encapsulates the two delivery paths:

- ``fanout_on_publish``: an episode just transitioned to published; insert
  one row per follower of its podcast (``follow_new`` source).
- ``seed_on_follow``: a user just followed a podcast; deliver up to N
  recent published episodes (``follow_seed`` source).

Plus the read/state-mutation APIs used by the inbox view.
"""

from datetime import datetime, timezone
from typing import List, Optional

from structlog import get_logger

from ..models.inbox import INBOX_STATES, InboxEntry, InboxItem
from ..repositories.inbox_repository import InboxRepository
from ..repositories.podcast_follower_repository import PodcastFollowerRepository

logger = get_logger(__name__)


class InboxServiceError(Exception):
    """Base exception for inbox service errors."""


class InvalidInboxStateError(InboxServiceError):
    """Raised when an unknown state value is passed to ``mark_state``."""


class InboxEntryNotFoundError(InboxServiceError):
    """Raised when ``mark_state`` targets a (user, episode) pair that has no row."""


class InboxService:
    """
    Service for delivering episodes to user inboxes and reading/mutating
    inbox state.
    """

    def __init__(
        self,
        inbox_repository: InboxRepository,
        follower_repository: PodcastFollowerRepository,
        *,
        seed_on_follow_count: int = 2,
    ) -> None:
        if seed_on_follow_count < 0:
            raise ValueError("seed_on_follow_count must be non-negative")
        self._repository = inbox_repository
        self._followers = follower_repository
        self._seed_count = seed_on_follow_count
        logger.info("InboxService initialized", seed_on_follow=seed_on_follow_count)

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def fanout_on_publish(self, episode_id: str, podcast_id: str) -> int:
        """
        Insert one inbox row per current follower of ``podcast_id``.

        Idempotent: re-running for the same episode is a no-op because the
        repository inserts with ``OR IGNORE`` on ``(user_id, episode_id)``.

        Returns the number of rows actually inserted.
        """
        follower_ids = self._followers.get_follower_user_ids(podcast_id)
        if not follower_ids:
            logger.debug("inbox_fanout_no_followers", episode_id=episode_id, podcast_id=podcast_id)
            return 0

        now = datetime.now(timezone.utc)
        entries = [
            InboxEntry(
                user_id=user_id,
                episode_id=episode_id,
                source="follow_new",
                state="unread",
                delivered_at=now,
            )
            for user_id in follower_ids
        ]
        inserted = self._repository.insert_many(entries)
        logger.info(
            "inbox_delivered",
            episode_id=episode_id,
            podcast_id=podcast_id,
            followers=len(follower_ids),
            inserted=inserted,
        )
        return inserted

    def seed_on_follow(self, user_id: str, podcast_id: str) -> int:
        """
        Deliver up to ``seed_on_follow_count`` recent published episodes to
        the user's inbox with ``source='follow_seed'``.

        Returns the number of rows actually inserted (may be 0 if the
        podcast has no published episodes yet, or if all the candidates were
        already delivered to this user via some prior path).
        """
        if self._seed_count == 0:
            return 0

        episode_ids = self._repository.recent_published_episode_ids(podcast_id, self._seed_count)
        if not episode_ids:
            logger.debug("inbox_seed_no_published_episodes", user_id=user_id, podcast_id=podcast_id)
            return 0

        now = datetime.now(timezone.utc)
        entries = [
            InboxEntry(
                user_id=user_id,
                episode_id=episode_id,
                source="follow_seed",
                state="unread",
                delivered_at=now,
            )
            for episode_id in episode_ids
        ]
        inserted = self._repository.insert_many(entries)
        logger.info(
            "inbox_seeded",
            user_id=user_id,
            podcast_id=podcast_id,
            candidates=len(episode_ids),
            inserted=inserted,
        )
        return inserted

    def mark_state(self, user_id: str, episode_id: str, state: str) -> InboxEntry:
        """
        Set ``state`` on the row for ``(user_id, episode_id)``.

        Raises:
            InvalidInboxStateError: if ``state`` is not a recognized value.
            InboxEntryNotFoundError: if no row exists for the pair.
        """
        if state not in INBOX_STATES:
            raise InvalidInboxStateError(f"Invalid state: {state!r} (expected one of {INBOX_STATES})")
        now = datetime.now(timezone.utc)
        entry = self._repository.update_state(user_id, episode_id, state, now)
        if entry is None:
            raise InboxEntryNotFoundError(f"No inbox row for user_id={user_id!r}, episode_id={episode_id!r}")
        logger.info("inbox_state_changed", user_id=user_id, episode_id=episode_id, state=state)
        return entry

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def list(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        """
        Return paginated inbox items, newest first.

        When ``state`` is None, dismissed rows are filtered out — the inbox
        is a triage view, not an audit log. Pass ``state='dismissed'``
        explicitly to list dismissals.
        """
        if state is not None and state not in INBOX_STATES:
            raise InvalidInboxStateError(f"Invalid state filter: {state!r} (expected one of {INBOX_STATES})")
        return self._repository.list_items(user_id, state=state, limit=limit, before=before)

    def unread_count(self, user_id: str) -> int:
        """Return the number of unread rows for ``user_id``."""
        return self._repository.unread_count(user_id)
