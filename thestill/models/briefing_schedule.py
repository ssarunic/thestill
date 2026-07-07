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
Per-user briefing schedule model (spec #50).

One row per user: when (``hour_local`` in ``timezone``) and how often
(``frequency``, with ``weekday`` for weekly) their briefing should be
generated. ``next_run_at`` is the materialized due-time in UTC — the
scheduler's due-scan reads it directly instead of recomputing cadence
per tick. ``NULL`` while disabled (the "parked" idiom from spec #48).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class BriefingFrequency(str, Enum):
    """How often a scheduled briefing fires."""

    DAILY = "daily"
    WEEKLY = "weekly"


class BriefingSchedule(BaseModel):
    """A user's briefing generation schedule (spec #50)."""

    user_id: str
    frequency: BriefingFrequency = BriefingFrequency.DAILY
    hour_local: int = Field(default=8, ge=0, le=23)
    weekday: Optional[int] = Field(default=None, ge=0, le=6)  # 0=Mon … 6=Sun
    timezone_name: str
    enabled: bool = True
    next_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("timezone_name")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        # Reject at write time, not at 8am (FM-4: no silent fallback to UTC).
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _weekday_iff_weekly(self) -> "BriefingSchedule":
        if (self.frequency == BriefingFrequency.WEEKLY) != (self.weekday is not None):
            raise ValueError("weekday is required for weekly frequency and must be unset for daily")
        return self

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)
