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
Abstract repository interface for briefing deliveries (spec #51).

Owns storage for ``briefing_deliveries`` rows plus the delivery pass's hot
operations: the constraint-anchored ``ensure_pending`` (send-once), the
indexed due-scan, the leased ``claim``, and the three settle transitions.
Backoff *math* lives in ``BriefingDeliveryService``; the repository never
computes it.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.briefing_delivery import BriefingDelivery


class BriefingDeliveryRepository(ABC):
    """Abstract repository for briefing delivery records."""

    @abstractmethod
    def ensure_pending(self, briefing_id: str, channel: str, *, now: datetime) -> bool:
        """Insert a pending delivery for ``(briefing_id, channel)`` if none
        exists — ``INSERT … ON CONFLICT DO NOTHING`` against the UNIQUE
        constraint, so racing triggers collapse to one delivery.

        Returns True when a new row was created, False when one already
        existed (any status — a sent or failed delivery is never reopened).
        """

    @abstractmethod
    def get_for_briefing(self, briefing_id: str, channel: str) -> Optional[BriefingDelivery]:
        """Return the delivery for ``(briefing_id, channel)``, or ``None``."""

    @abstractmethod
    def due(self, now: datetime, *, limit: int) -> List[BriefingDelivery]:
        """Claimable deliveries, oldest ``next_attempt_at`` first.

        Covers ``pending`` rows whose ``next_attempt_at`` has passed and
        ``sending`` rows whose claim lease (also ``next_attempt_at``) has
        expired — the crashed-mid-send recovery path.
        """

    @abstractmethod
    def claim(self, delivery_id: str, *, now: datetime, lease_seconds: int) -> bool:
        """Atomically take a due delivery: ``status → 'sending'`` and
        ``next_attempt_at → now + lease`` iff the row is still claimable
        (same conditional-UPDATE idiom as the #50 slot claim).

        Returns True when this caller won the row; multi-instance
        deployments can't double-send within the lease.
        """

    @abstractmethod
    def mark_sent(self, delivery_id: str, *, sent_at: datetime) -> None:
        """Terminal success: ``status='sent'``, ``next_attempt_at=NULL``."""

    @abstractmethod
    def mark_retry(self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str) -> None:
        """Failed attempt with retries left: back to ``pending`` with the
        provided backoff time and error, ``attempts`` updated."""

    @abstractmethod
    def mark_failed(self, delivery_id: str, *, attempts: int, error: str) -> None:
        """Terminal failure: parked as ``failed`` (FM-1 — never burns every
        tick), ``next_attempt_at=NULL`` so the due-scan skips it."""
