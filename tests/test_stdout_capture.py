# Copyright 2025 thestill.me
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

"""Tests for stdout capture utility."""

import io
import re
import sys

import pytest

from thestill.utils.stdout_capture import WHISPERX_PROGRESS_PATTERN, StdoutProgressCapture


class TestStdoutProgressCapture:
    """Tests for StdoutProgressCapture context manager."""

    def test_pattern_matching_extracts_integer_value(self) -> None:
        """Test that integer progress values are extracted correctly."""
        captured_values: list[float] = []
        pattern = re.compile(r"Progress: (\d+)%")

        with StdoutProgressCapture(pattern, captured_values.append, passthrough=False):
            print("Progress: 50%")

        assert captured_values == [50.0]

    def test_pattern_matching_extracts_float_value(self) -> None:
        """Test that float progress values are extracted correctly."""
        captured_values: list[float] = []

        with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, captured_values.append, passthrough=False):
            print("Progress: 33.33%...")

        assert captured_values == [33.33]

    def test_callback_invoked_multiple_times(self) -> None:
        """Test that callback is invoked for each match."""
        captured_values: list[float] = []

        with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, captured_values.append, passthrough=False):
            print("Progress: 25.00%...")
            print("Progress: 50.00%...")
            print("Progress: 75.00%...")
            print("Progress: 100.00%...")

        assert captured_values == [25.0, 50.0, 75.0, 100.0]

    def test_non_matching_text_ignored(self) -> None:
        """Test that non-matching text doesn't trigger callback."""
        captured_values: list[float] = []

        with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, captured_values.append, passthrough=False):
            print("Loading model...")
            print("Processing audio...")
            print("Done!")

        assert captured_values == []

    def test_passthrough_writes_to_original_stdout(self) -> None:
        """Test that passthrough mode writes to original stdout."""
        original_stdout = sys.stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            captured_values: list[float] = []
            with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, captured_values.append, passthrough=True):
                print("Progress: 50.00%...")
        finally:
            sys.stdout = original_stdout

        assert "Progress: 50.00%..." in captured_output.getvalue()
        assert captured_values == [50.0]

    def test_passthrough_disabled_does_not_write(self) -> None:
        """Test that passthrough=False doesn't write to original stdout."""
        original_stdout = sys.stdout
        captured_output = io.StringIO()
        sys.stdout = captured_output

        try:
            captured_values: list[float] = []
            with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, captured_values.append, passthrough=False):
                print("Progress: 50.00%...")
        finally:
            sys.stdout = original_stdout

        assert captured_output.getvalue() == ""
        assert captured_values == [50.0]

    def test_stdout_restored_on_normal_exit(self) -> None:
        """Test that stdout is restored after context manager exits normally."""
        original_stdout = sys.stdout

        with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, lambda x: None, passthrough=False):
            assert sys.stdout is not original_stdout

        assert sys.stdout is original_stdout

    def test_stdout_restored_on_exception(self) -> None:
        """Test that stdout is restored even when exception is raised."""
        original_stdout = sys.stdout

        with pytest.raises(ValueError):
            with StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, lambda x: None, passthrough=False):
                assert sys.stdout is not original_stdout
                raise ValueError("Test exception")

        assert sys.stdout is original_stdout

    def test_write_returns_character_count(self) -> None:
        """Test that write() returns the number of characters written."""
        capture = StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, lambda x: None, passthrough=False)
        capture._original_stdout = io.StringIO()

        result = capture.write("Hello, World!")
        assert result == 13

    def test_flush_calls_original_stdout_flush(self) -> None:
        """Test that flush() calls flush on original stdout."""
        mock_stdout = io.StringIO()
        capture = StdoutProgressCapture(WHISPERX_PROGRESS_PATTERN, lambda x: None, passthrough=False)
        capture._original_stdout = mock_stdout

        # This should not raise an error
        capture.flush()

    def test_invalid_capture_group_handled_gracefully(self) -> None:
        """Test that invalid regex capture groups don't crash."""
        captured_values: list[float] = []
        # Pattern without capture group
        pattern = re.compile(r"Progress: \d+%")

        with StdoutProgressCapture(pattern, captured_values.append, passthrough=False):
            print("Progress: 50%")

        # Should not have captured anything due to missing capture group
        assert captured_values == []

    def test_non_numeric_capture_group_handled_gracefully(self) -> None:
        """Test that non-numeric capture values don't crash."""
        captured_values: list[float] = []
        # Pattern that captures non-numeric text
        pattern = re.compile(r"Status: (\w+)")

        with StdoutProgressCapture(pattern, captured_values.append, passthrough=False):
            print("Status: running")

        # Should not have captured anything due to non-numeric value
        assert captured_values == []


class TestWhisperXProgressPattern:
    """Tests for the pre-compiled WhisperX progress pattern."""

    def test_matches_integer_percentage(self) -> None:
        """Test pattern matches integer percentage."""
        match = WHISPERX_PROGRESS_PATTERN.search("Progress: 50%...")
        assert match is not None
        assert match.group(1) == "50"

    def test_matches_float_percentage(self) -> None:
        """Test pattern matches float percentage."""
        match = WHISPERX_PROGRESS_PATTERN.search("Progress: 33.33%...")
        assert match is not None
        assert match.group(1) == "33.33"

    def test_matches_zero_percentage(self) -> None:
        """Test pattern matches zero percentage."""
        match = WHISPERX_PROGRESS_PATTERN.search("Progress: 0%...")
        assert match is not None
        assert match.group(1) == "0"

    def test_matches_hundred_percentage(self) -> None:
        """Test pattern matches 100 percentage."""
        match = WHISPERX_PROGRESS_PATTERN.search("Progress: 100.00%...")
        assert match is not None
        assert match.group(1) == "100.00"

    def test_does_not_match_other_formats(self) -> None:
        """Test pattern doesn't match non-WhisperX formats."""
        non_matches = [
            "Loading: 50%",
            "50% complete",
            "Progress 50%",
            "Progress: fifty%",
        ]
        for text in non_matches:
            assert WHISPERX_PROGRESS_PATTERN.search(text) is None
