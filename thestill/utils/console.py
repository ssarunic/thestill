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

import sys


class ConsoleOutput:
    """User-facing console output for CLI commands.

    Outputs to stdout by default, can be suppressed with quiet mode.
    Separate from backend logging (structlog → stderr).

    This class is designed for user-facing messages in CLI commands,
    while backend logging (using structlog) goes to stderr for
    structured logging and observability.
    """

    def __init__(self, quiet: bool = False):
        """Initialize console output.

        Args:
            quiet: If True, suppress all output except errors
        """
        self.quiet = quiet

    def info(self, message: str) -> None:
        """Print an informational message.

        Args:
            message: The message to print
        """
        if not self.quiet:
            sys.stdout.write(f"{message}\n")
            sys.stdout.flush()

    def success(self, message: str) -> None:
        """Print a success message with checkmark.

        Args:
            message: The message to print
        """
        if not self.quiet:
            sys.stdout.write(f"✓ {message}\n")
            sys.stdout.flush()

    def error(self, message: str) -> None:
        """Print an error message with X mark.

        Args:
            message: The error message to print
        """
        sys.stderr.write(f"✗ {message}\n")
        sys.stderr.flush()

    def warning(self, message: str) -> None:
        """Print a warning message with warning symbol.

        Args:
            message: The warning message to print
        """
        if not self.quiet:
            sys.stdout.write(f"⚠ {message}\n")
            sys.stdout.flush()

    def progress(self, message: str) -> None:
        """Print a progress message with hourglass.

        Args:
            message: The progress message to print
        """
        if not self.quiet:
            sys.stdout.write(f"⏳ {message}\n")
            sys.stdout.flush()

    def progress_complete(self, message: str) -> None:
        """Print a progress completion message with checkmark.

        Args:
            message: The completion message to print
        """
        if not self.quiet:
            sys.stdout.write(f"✓ {message}\n")
            sys.stdout.flush()
