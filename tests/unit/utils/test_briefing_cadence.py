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

"""Unit tests for scheduled-briefing cadence math (spec #50).

Pure-function tests: DST transitions, weekly wrap-around, and the
"strictly after" contract are all pinned with explicit ``after`` instants,
no clock patching.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from thestill.models.briefing_schedule import BriefingFrequency, BriefingSchedule
from thestill.utils.briefing_cadence import next_occurrence, next_run_for

UTC = timezone.utc
ZAGREB = ZoneInfo("Europe/Zagreb")  # CEST (+2) in July, CET (+1) in winter
NEW_YORK = ZoneInfo("America/New_York")


def _daily(hour: int, tz, after: datetime) -> datetime:
    return next_occurrence(frequency=BriefingFrequency.DAILY, hour_local=hour, weekday=None, tz=tz, after=after)


def _weekly(hour: int, weekday: int, tz, after: datetime) -> datetime:
    return next_occurrence(frequency=BriefingFrequency.WEEKLY, hour_local=hour, weekday=weekday, tz=tz, after=after)


# ============================================================================
# Daily
# ============================================================================


class TestDaily:
    def test_same_day_when_hour_still_ahead(self):
        # 05:00 UTC = 07:00 in Zagreb (CEST); 8am local is still ahead today.
        after = datetime(2026, 7, 7, 5, 0, tzinfo=UTC)
        result = _daily(8, ZAGREB, after)
        assert result == datetime(2026, 7, 7, 6, 0, tzinfo=UTC)  # 08:00 CEST
        assert result.tzinfo == UTC

    def test_next_day_when_hour_already_passed(self):
        # 10:00 UTC = 12:00 CEST; 8am local already passed → tomorrow.
        after = datetime(2026, 7, 7, 10, 0, tzinfo=UTC)
        assert _daily(8, ZAGREB, after) == datetime(2026, 7, 8, 6, 0, tzinfo=UTC)

    def test_strictly_after_at_exact_occurrence(self):
        # Exactly 08:00 local → the *next* day, never "now".
        after = datetime(2026, 7, 7, 6, 0, tzinfo=UTC)  # == 08:00 CEST
        assert _daily(8, ZAGREB, after) == datetime(2026, 7, 8, 6, 0, tzinfo=UTC)

    def test_catchup_is_single_slot_not_replay(self):
        # Three days behind or three minutes behind: same single next slot.
        slot = datetime(2026, 7, 7, 6, 0, tzinfo=UTC)  # 08:00 CEST
        three_days_late = _daily(8, ZAGREB, slot + timedelta(days=3, hours=3))
        three_minutes_late = _daily(8, ZAGREB, slot + timedelta(days=3, hours=3, minutes=-177))
        assert three_days_late == datetime(2026, 7, 11, 6, 0, tzinfo=UTC)
        assert three_days_late == three_minutes_late

    def test_midnight_hour(self):
        after = datetime(2026, 7, 7, 23, 30, tzinfo=UTC)  # 01:30 CEST July 8
        assert _daily(0, ZAGREB, after) == datetime(2026, 7, 8, 22, 0, tzinfo=UTC)  # 00:00 CEST July 9


# ============================================================================
# Weekly
# ============================================================================


class TestWeekly:
    def test_later_this_week(self):
        # Tuesday July 7 2026 → Monday(0) 8am lands Monday July 13.
        after = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
        assert _weekly(8, 0, ZAGREB, after) == datetime(2026, 7, 13, 6, 0, tzinfo=UTC)

    def test_same_day_hour_ahead(self):
        # Tuesday(1) morning, before 8am local → fires today.
        after = datetime(2026, 7, 7, 4, 0, tzinfo=UTC)  # 06:00 CEST Tuesday
        assert _weekly(8, 1, ZAGREB, after) == datetime(2026, 7, 7, 6, 0, tzinfo=UTC)

    def test_same_day_hour_passed_wraps_a_full_week(self):
        after = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)  # Tuesday 14:00 CEST
        assert _weekly(8, 1, ZAGREB, after) == datetime(2026, 7, 14, 6, 0, tzinfo=UTC)

    def test_month_and_year_boundary(self):
        # Wednesday Dec 30 2026 → next Monday(0) is Jan 4 2027, CET (+1).
        after = datetime(2026, 12, 30, 12, 0, tzinfo=UTC)
        assert _weekly(8, 0, ZAGREB, after) == datetime(2027, 1, 4, 7, 0, tzinfo=UTC)


# ============================================================================
# DST transitions (America/New_York: spring forward 2026-03-08 02:00→03:00,
# fall back 2026-11-01 02:00→01:00)
# ============================================================================


class TestDst:
    def test_spring_forward_nonexistent_hour_resolves_post_transition(self):
        # 02:00 local doesn't exist on 2026-03-08. fold=0 applies the
        # pre-transition offset (EST, -5) → 07:00 UTC, which is 03:00 EDT:
        # the post-transition instant, not a skipped day.
        after = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)  # 00:00 EST
        assert _daily(2, NEW_YORK, after) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)

    def test_fall_back_ambiguous_hour_takes_first_occurrence(self):
        # 01:00 local happens twice on 2026-11-01. fold=0 → EDT (-4) → 05:00 UTC.
        after = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 00:00 EDT
        assert _daily(1, NEW_YORK, after) == datetime(2026, 11, 1, 5, 0, tzinfo=UTC)

    def test_utc_offset_shifts_across_transition(self):
        # Daily 8am spans the spring-forward: 13:00 UTC before, 12:00 UTC after.
        before = _daily(8, NEW_YORK, datetime(2026, 3, 7, 0, 0, tzinfo=UTC))
        after_transition = _daily(8, NEW_YORK, before)
        assert before == datetime(2026, 3, 7, 13, 0, tzinfo=UTC)  # EST
        assert after_transition == datetime(2026, 3, 8, 12, 0, tzinfo=UTC)  # EDT


# ============================================================================
# Contract / validation
# ============================================================================


class TestContract:
    def test_rejects_naive_after(self):
        # The naive datetime is deliberate: FM-3 requires the rejection.
        with pytest.raises(ValueError, match="timezone-aware"):
            _daily(8, ZAGREB, datetime(2026, 7, 7, 5, 0))  # noqa: DTZ001

    def test_rejects_weekday_on_daily(self):
        with pytest.raises(ValueError, match="weekday"):
            next_occurrence(
                frequency=BriefingFrequency.DAILY,
                hour_local=8,
                weekday=0,
                tz=ZAGREB,
                after=datetime(2026, 7, 7, 5, 0, tzinfo=UTC),
            )

    def test_rejects_missing_weekday_on_weekly(self):
        with pytest.raises(ValueError, match="weekday"):
            next_occurrence(
                frequency=BriefingFrequency.WEEKLY,
                hour_local=8,
                weekday=None,
                tz=ZAGREB,
                after=datetime(2026, 7, 7, 5, 0, tzinfo=UTC),
            )

    def test_next_run_for_reads_schedule_fields(self):
        schedule = BriefingSchedule(
            user_id="00000000-0000-0000-0000-000000000001",
            frequency=BriefingFrequency.WEEKLY,
            hour_local=8,
            weekday=0,
            timezone_name="Europe/Zagreb",
        )
        after = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
        assert next_run_for(schedule, after=after) == datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
