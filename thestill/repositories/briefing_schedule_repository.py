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
Abstract repository interface for briefing schedules (spec #50).

The repository owns storage for ``user_briefing_schedules`` rows plus the
scheduler's two hot operations: the indexed due-scan and the
advance-before-generate ``claim``. Cadence math (what the *next* due time
is) lives in ``utils.briefing_cadence``; the repository never computes it.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.briefing_schedule import BriefingSchedule


class BriefingScheduleRepository(ABC):
    """Abstract repository for per-user briefing schedules."""

    @abstractmethod
    def get(self, user_id: str) -> Optional[BriefingSchedule]:
        """Return the user's schedule, or ``None`` if never configured."""

    @abstractmethod
    def upsert(self, schedule: BriefingSchedule) -> BriefingSchedule:
        """Insert or replace the user's schedule row (one per user)."""

    @abstractmethod
    def due(self, now: datetime, *, limit: int) -> List[BriefingSchedule]:
        """Enabled schedules with ``next_run_at <= now``, oldest first."""

    @abstractmethod
    def claim(self, user_id: str, *, expected_next_run_at: datetime, new_next_run_at: datetime) -> bool:
        """Atomically advance ``next_run_at`` iff it still equals
        ``expected_next_run_at`` (and the schedule is still enabled).

        Returns True when this caller won the slot. The conditional guard
        makes the scheduler tick safe under multiple server instances and
        guarantees a crashed generation doesn't re-fire every tick.
        """

    @abstractmethod
    def set_email_enabled(self, user_id: str, enabled: bool) -> bool:
        """Flip only the email opt-in flag (spec #51 unsubscribe path).

        A targeted UPDATE rather than a full upsert so it can't race the
        scheduler's ``claim`` (which rewrites ``next_run_at``). Returns True
        when a schedule row existed and was updated.
        """
