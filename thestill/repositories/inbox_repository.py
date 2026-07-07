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
Abstract repository interface for per-user inbox persistence.

The repository is the storage contract for ``user_episode_inbox`` rows.
Service-layer concerns (which followers to fan out to, how many seed
episodes to pick) live in ``InboxService``.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from ..models.inbox import INBOX_STATES_ELIGIBLE_FOR_BRIEFING, InboxEntry, InboxItem, InboxState


class InboxRepository(ABC):
    """
    Abstract repository for per-user inbox persistence.

    Implementations must be thread-safe and idempotent on the
    ``(user_id, episode_id)`` uniqueness constraint.
    """

    @abstractmethod
    def insert_many(self, entries: List[InboxEntry]) -> int:
        """
        Insert a batch of inbox rows, ignoring conflicts on
        ``(user_id, episode_id)``.

        Returns the number of rows actually inserted (existing pairs are a
        no-op so the count can be less than ``len(entries)``).
        """

    @abstractmethod
    def find_or_create(self, *, user_id: str, episode_id: str, source: str) -> Tuple[InboxEntry, bool]:
        """
        Idempotent single-row insert (spec #31).

        Used by import / ad-hoc paths where the caller wants to know
        whether the inbox row was newly created (so it can drive a
        toast / progress indicator) or already existed.

        Returns ``(entry, created)``. When ``created=False``, ``source``
        on the returned entry reflects the *original* row, not the
        argument — re-importing an episode does not change provenance.
        """

    @abstractmethod
    def get(self, user_id: str, episode_id: str) -> Optional[InboxEntry]:
        """Return the inbox row for ``(user_id, episode_id)`` or ``None``."""

    @abstractmethod
    def update_state(
        self, user_id: str, episode_id: str, state: str, state_changed_at: datetime
    ) -> Optional[InboxEntry]:
        """
        Set ``state`` and ``state_changed_at`` for the row.

        Returns the updated row, or ``None`` if no row exists for the pair.
        """

    @abstractmethod
    def mark_read_if_unread(self, user_id: str, episode_id: str, state_changed_at: datetime) -> bool:
        """
        Transition the row to ``read`` only when it is currently ``unread``.

        Guarded variant of ``update_state`` for view-driven read tracking
        (spec #29): callers fire it without knowing whether an inbox row
        exists or what state it holds, so it must never clobber ``saved``
        or resurrect ``dismissed``.

        Returns ``True`` if a row actually transitioned; ``False`` when the
        row is absent or already in a non-``unread`` state.
        """

    @abstractmethod
    def list_items(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        """
        List inbox items for a user, newest-delivered first.

        - ``state`` is None: return everything except dismissed (the inbox
          triage view).
        - ``state`` set: return only rows in that state. Pass
          ``state='dismissed'`` to surface dismissed rows.
        - ``before``: cursor — return rows with ``delivered_at < before``.
        """

    @abstractmethod
    def unread_count(self, user_id: str) -> int:
        """Return the number of unread rows for the user."""

    @abstractmethod
    def recent_published_episode_ids(self, podcast_id: str, limit: int) -> List[str]:
        """
        Return the ``limit`` most-recently-published episode IDs for a podcast,
        ordered by ``published_at DESC``. Episodes with NULL ``published_at``
        are excluded — they haven't been delivered to anyone yet.
        """

    @abstractmethod
    def list_episode_ids_in_window(
        self,
        user_id: str,
        *,
        since: datetime,
        until: datetime,
        states: Iterable[InboxState] = INBOX_STATES_ELIGIBLE_FOR_BRIEFING,
    ) -> List[str]:
        """
        Return episode IDs of inbox rows delivered in ``[since, until)`` whose
        ``state`` is in ``states``, ordered oldest-delivered first.

        Used by the briefing path (spec #36) to compose the candidate set for
        a single briefing window.
        """

    @abstractmethod
    def backfill_existing_followers(self, limit: int, *, dry_run: bool = False) -> int:
        """
        Seed every existing follower's inbox with the podcast's most recent
        ``limit`` published episodes. Idempotent on the
        ``(user_id, episode_id)`` unique constraint.

        Returns the number of rows that were (or would be) inserted.
        ``dry_run=True`` reports the candidate count without writing.
        """

    @abstractmethod
    def count_imports_for_user_since(self, user_id: str, since: datetime) -> int:
        """
        Number of ``source='import'`` rows for ``user_id`` whose
        ``delivered_at`` is at or after ``since``.

        Plumbing for a future per-user import quota: the current import
        flow only emits the count as a structured-log field; nothing is
        enforced yet.
        """
