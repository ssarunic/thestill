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

"""Unit tests for the canonical tz-aware datetime helpers (spec #42)."""

from datetime import datetime, timezone
from time import struct_time

from thestill.utils.datetime_utils import ensure_utc, now_utc, parse_struct_time_utc


class TestNowUtc:
    def test_is_timezone_aware_utc(self):
        result = now_utc()
        assert result.tzinfo is not None
        assert result.utcoffset() == timezone.utc.utcoffset(None)

    def test_isoformat_carries_offset(self):
        # The SQLite repos store ``.isoformat()``; the project requires the
        # ISO-8601 ``+00:00`` offset, never a bare naive string.
        assert now_utc().isoformat().endswith("+00:00")


class TestEnsureUtc:
    def test_none_passthrough(self):
        assert ensure_utc(None) is None

    def test_naive_is_stamped_utc_not_shifted(self):
        naive = datetime(2026, 5, 21, 7, 7, 0)
        result = ensure_utc(naive)
        assert result.tzinfo == timezone.utc
        # Stamped, not shifted: wall-clock components are preserved.
        assert (result.year, result.month, result.day, result.hour, result.minute) == (2026, 5, 21, 7, 7)

    def test_aware_returned_unchanged(self):
        aware = datetime(2026, 5, 21, 7, 7, 0, tzinfo=timezone.utc)
        assert ensure_utc(aware) == aware

    def test_result_compares_against_aware_without_typeerror(self):
        # The whole point: a coerced value must be comparable to an aware one.
        assert ensure_utc(datetime(2026, 5, 21)) < now_utc()


class TestParseStructTimeUtc:
    def test_struct_time_to_aware_utc(self):
        st = struct_time((2026, 5, 21, 7, 7, 0, 0, 0, 0))
        result = parse_struct_time_utc(st)
        assert result == datetime(2026, 5, 21, 7, 7, 0, tzinfo=timezone.utc)

    def test_none_falls_back_to_now_utc(self):
        result = parse_struct_time_utc(None)
        assert result.tzinfo == timezone.utc

    def test_malformed_falls_back_to_aware_now(self):
        # A non-struct_time that blows up the constructor must still yield a
        # tz-aware value, never a naive one or an exception.
        result = parse_struct_time_utc("not-a-date-tuple")
        assert result.tzinfo == timezone.utc
