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

"""Tests for ``parse_target_duration`` + helpers (spec #33 Phase 3+4)."""

import pytest

from thestill.utils.duration import (
    NARRATION_DURATION_PRESETS,
    parse_target_duration,
    resolve_target_or_default,
    slug_for_duration_seconds,
)


@pytest.mark.parametrize(
    "preset,expected",
    list(NARRATION_DURATION_PRESETS.items()),
)
def test_named_presets(preset: str, expected: int) -> None:
    assert parse_target_duration(preset) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5m", 300),
        ("10m", 600),
        ("2h", 7200),
        ("120s", 120),
        ("0:05:00", 300),
        ("00:05:00", 300),
        ("5:30", 330),
        ("300", 300),
    ],
)
def test_unit_and_clock_forms(raw: str, expected: int) -> None:
    assert parse_target_duration(raw) == expected


def test_preset_is_case_insensitive() -> None:
    assert parse_target_duration("SHORT") == 180
    assert parse_target_duration("Medium") == 300


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "abc", "5", "0", "0s", "-5m"],
)
def test_invalid_inputs_raise_value_error(raw: str) -> None:
    if raw == "5" or raw == "0":
        # bare integers are valid seconds via parse_duration; "0" raises
        # because we reject zero. "5" returns 5 (valid seconds).
        if raw == "5":
            assert parse_target_duration(raw) == 5
            return
    with pytest.raises(ValueError):
        parse_target_duration(raw)


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (180, "short"),
        (300, "medium"),
        (600, "long"),
        (450, "custom-450s"),
        (12, "custom-12s"),
    ],
)
def test_slug_for_duration_seconds(seconds: int, expected: str) -> None:
    assert slug_for_duration_seconds(seconds) == expected


def test_slug_round_trips_with_preset_parser() -> None:
    """The slug returned for a preset duration must parse back to that duration."""
    for seconds in (180, 300, 600):
        slug = slug_for_duration_seconds(seconds)
        assert parse_target_duration(slug) == seconds


class TestResolveTargetOrDefault:
    def test_none_falls_back_to_default(self) -> None:
        assert resolve_target_or_default(None, default=300) == 300

    def test_positive_int_pass_through(self) -> None:
        assert resolve_target_or_default(450, default=300) == 450

    def test_zero_int_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_target_or_default(0, default=300)

    def test_negative_int_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_target_or_default(-1, default=300)

    def test_preset_string_resolves(self) -> None:
        assert resolve_target_or_default("short", default=300) == 180

    def test_clock_string_resolves(self) -> None:
        assert resolve_target_or_default("0:05:00", default=120) == 300

    def test_unparseable_string_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_target_or_default("nope", default=300)
