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
from typing import List, Optional

from ..models.inbox import InboxEntry, InboxItem


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
