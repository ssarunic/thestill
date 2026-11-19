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
Abstract repository for the lifecycle message ledger (spec #118).

Two halves:

- the ledger (``user_messages``): the constraint-anchored
  ``ensure_pending`` (send-once), the indexed due-scan, the leased
  ``claim`` and the settle transitions, the same shape as
  ``BriefingDeliveryRepository`` (spec #51);
- ``candidates``: one read across product tables that returns
  ``LifecycleFacts`` for the users the planner may write to.

Which message is due, the frequency cap and the backoff math live in
``LifecycleEmailService``; the repository never decides.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from ..models.user_message import LifecycleFacts, UserMessage


class UserMessageRepository(ABC):
    """Abstract repository for lifecycle message records."""

    @abstractmethod
    def candidates(
        self,
        *,
        created_from: datetime,
        created_to: datetime,
        channel: str,
        limit: int,
        user_id: Optional[str] = None,
    ) -> List[LifecycleFacts]:
        """Users who may get a lifecycle message, oldest signup first.

        Only accounts that are ``active``, have "Product tips by email" on
        and were created in ``[created_from, created_to]``. With ``user_id``,
        just that user (the send-time re-check); empty when they no longer
        qualify.
        """

    @abstractmethod
    def ensure_pending(self, user_id: str, message_key: str, channel: str, *, now: datetime) -> bool:
        """Insert a pending row for ``(user_id, message_key, channel)`` if
        none exists (``ON CONFLICT DO NOTHING``), due at ``now``.

        Returns True when a new row was created; False when one already
        existed in any status (a sent, failed or skipped message is never
        reopened).
        """

    @abstractmethod
    def get(self, message_id: str) -> Optional[UserMessage]:
        """Return the row, or ``None``."""

    @abstractmethod
    def due(self, now: datetime, *, limit: int) -> List[UserMessage]:
        """Claimable rows, oldest ``next_attempt_at`` first: ``pending``
        rows that are due and ``sending`` rows whose lease has expired."""

    @abstractmethod
    def claim(self, message_id: str, *, now: datetime, lease_seconds: int) -> bool:
        """Atomically take a due row: ``status → 'sending'``,
        ``next_attempt_at → now + lease``, ``attempts → attempts + 1``, iff
        it is still claimable. Returns True when this caller won it."""

    @abstractmethod
    def mark_sent(self, message_id: str, *, sent_at: datetime) -> None:
        """Terminal success."""

    @abstractmethod
    def mark_retry(self, message_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        """Failed attempt with retries left: back to ``pending``."""

    @abstractmethod
    def mark_failed(self, message_id: str, *, attempts: int, error: str) -> None:
        """Terminal failure, parked so the due-scan skips it."""

    @abstractmethod
    def mark_skipped(self, message_id: str, *, reason: str) -> None:
        """Terminal: the message no longer applied at send time."""

    @abstractmethod
    def status_counts(self) -> Dict[Tuple[str, str], int]:
        """``{(message_key, status): count}`` over the whole ledger, for
        ``thestill lifecycle status``."""
