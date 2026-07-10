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
Abstract repository interface for per-user briefing persistence (spec #36).

The repository owns the storage contract for ``user_briefings`` rows.
Cursor math, throttle windows, and inbox composition live in
``BriefingService``.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.briefing import Briefing


class BriefingRepository(ABC):
    """Abstract repository for per-user briefing persistence."""

    @abstractmethod
    def insert(self, briefing: Briefing) -> Briefing:
        """Persist a new briefing row. Returns the inserted briefing."""

    @abstractmethod
    def get(self, briefing_id: str) -> Optional[Briefing]:
        """Return the briefing for ``briefing_id`` or ``None``."""

    @abstractmethod
    def latest_for_user(self, user_id: str) -> Optional[Briefing]:
        """Return the user's most recently-created briefing, or ``None``."""

    @abstractmethod
    def count_pending_for_user(
        self,
        user_id: str,
        *,
        since: datetime,
        cutoff: datetime,
    ) -> int:
        """Count followed, pre-cutoff episodes in the open briefing window
        that have not reached ``user_id``'s inbox and still have active queue
        work (spec #55)."""

    @abstractmethod
    def list_for_user(self, user_id: str, *, limit: int, offset: int) -> List[Briefing]:
        """Return the user's briefings, newest first (paginated history)."""

    @abstractmethod
    def count_for_user(self, user_id: str) -> int:
        """Total briefings for the user (pagination metadata)."""

    @abstractmethod
    def update_listened_at(self, briefing_id: str, listened_at: datetime) -> Optional[Briefing]:
        """Set ``listened_at`` on the row. Returns the updated briefing,
        or ``None`` if no row exists."""
