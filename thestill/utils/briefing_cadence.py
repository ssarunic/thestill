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
Cadence math for scheduled briefings (spec #50).

Pure and clock-free: callers pass ``after`` explicitly, so tests can pin
DST transitions and week boundaries without patching the clock. The
occurrence is computed as a wall-clock time in the user's IANA zone and
returned as a UTC instant (FM-3: everything stored/compared in UTC).

DST notes (zoneinfo ``fold=0`` semantics):
- A nonexistent local time (spring-forward gap, e.g. 02:30 during a
  02:00→03:00 jump) converts to the post-transition instant.
- An ambiguous local time (fall-back repeat) takes the first occurrence.

Recomputing "next occurrence strictly after *now*" — rather than
repeatedly adding one period to the stored due-time — is what makes
downtime catch-up fire exactly once: being three days behind or three
minutes behind produces the same single next slot.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ..models.briefing_schedule import BriefingFrequency, BriefingSchedule


def next_occurrence(
    *,
    frequency: BriefingFrequency,
    hour_local: int,
    weekday: Optional[int],
    tz: ZoneInfo,
    after: datetime,
) -> datetime:
    """Next occurrence of the schedule strictly after ``after``, in UTC.

    ``weekday`` follows ``date.weekday()`` (0=Monday … 6=Sunday) and is
    required iff ``frequency`` is weekly.
    """
    if after.tzinfo is None:
        raise ValueError("`after` must be timezone-aware (FM-3)")
    if not 0 <= hour_local <= 23:
        raise ValueError(f"hour_local out of range: {hour_local}")
    if (frequency == BriefingFrequency.WEEKLY) != (weekday is not None):
        raise ValueError("weekday is required iff frequency is weekly")

    local_after = after.astimezone(tz)
    candidate_date = local_after.date()
    if weekday is not None:
        candidate_date += timedelta(days=(weekday - candidate_date.weekday()) % 7)
    step = timedelta(days=7 if frequency == BriefingFrequency.WEEKLY else 1)

    candidate = _at_local_hour(candidate_date, hour_local, tz)
    # "Strictly after" is decided on instants, not wall clocks, so a DST
    # shift can't produce a candidate that is nominally later but already
    # past. At most two iterations.
    while candidate <= after:
        candidate_date += step
        candidate = _at_local_hour(candidate_date, hour_local, tz)
    return candidate.astimezone(timezone.utc)


def next_run_for(schedule: BriefingSchedule, *, after: datetime) -> datetime:
    """``next_occurrence`` over a schedule row's fields."""
    return next_occurrence(
        frequency=schedule.frequency,
        hour_local=schedule.hour_local,
        weekday=schedule.weekday,
        tz=schedule.tzinfo,
        after=after,
    )


def _at_local_hour(day: date, hour: int, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=tz)
