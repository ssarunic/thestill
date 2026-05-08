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

"""Tests for ``parse_target_duration`` (spec #33 Phase 3)."""

import pytest

from thestill.utils.duration import (
    NARRATION_DURATION_PRESETS,
    parse_target_duration,
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
