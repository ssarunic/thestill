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

"""
Stdout capture utility for parsing progress output from external libraries.

This module provides a context manager that intercepts stdout writes,
parses them against a regex pattern, and invokes a callback when matches
are found. Used primarily to capture WhisperX progress output.
"""

import re
import sys
from typing import Callable, Optional, Pattern


class StdoutProgressCapture:
    """
    Context manager to intercept stdout and parse progress patterns.

    Captures stdout writes, passes them through to the original stdout,
    and invokes a callback when text matches the provided regex pattern.

    Example:
        >>> import re
        >>> pattern = re.compile(r"Progress: (\\d+\\.?\\d*)%")
        >>> def on_progress(pct):
        ...     print(f"Got {pct}%", file=sys.stderr)
        >>> with StdoutProgressCapture(pattern, on_progress):
        ...     print("Progress: 50.00%...")  # Triggers callback
    """

    def __init__(
        self,
        pattern: Pattern[str],
        callback: Callable[[float], None],
        passthrough: bool = True,
    ):
        """
        Initialize the stdout capture.

        Args:
            pattern: Compiled regex with one capture group for the progress value
            callback: Function called with the captured float value on match
            passthrough: If True, also write to original stdout (default: True)
        """
        self.pattern = pattern
        self.callback = callback
        self.passthrough = passthrough
        self._original_stdout: Optional[object] = None

    def __enter__(self) -> "StdoutProgressCapture":
        """Replace sys.stdout with this capture instance."""
        self._original_stdout = sys.stdout
        sys.stdout = self  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Restore original stdout."""
        if self._original_stdout is not None:
            sys.stdout = self._original_stdout  # type: ignore[assignment]
            self._original_stdout = None

    def write(self, text: str) -> int:
        """
        Write text, optionally passing through and checking for pattern match.

        Args:
            text: Text being written to stdout

        Returns:
            Number of characters written
        """
        # Pass through to original stdout if enabled
        if self.passthrough and self._original_stdout is not None:
            self._original_stdout.write(text)  # type: ignore[union-attr]

        # Check for pattern match
        match = self.pattern.search(text)
        if match:
            try:
                progress_value = float(match.group(1))
                self.callback(progress_value)
            except (ValueError, IndexError):
                # Invalid match or no capture group - ignore
                pass

        return len(text)

    def flush(self) -> None:
        """Flush the original stdout."""
        if self._original_stdout is not None:
            self._original_stdout.flush()  # type: ignore[union-attr]


# Pre-compiled pattern for WhisperX progress output
WHISPERX_PROGRESS_PATTERN = re.compile(r"Progress: (\d+\.?\d*)%")
