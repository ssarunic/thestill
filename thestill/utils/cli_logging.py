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

"""CLI command logging decorator for lifecycle tracking.

This module provides structured logging for CLI commands, tracking:
- Command invocation with unique command_id
- Arguments passed to the command (sanitized)
- Execution duration
- Success/failure status
- Exceptions with stack traces
"""

import time
import uuid
from functools import wraps
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

# Environment variables and secrets to redact from logs
SENSITIVE_KEYS = {
    "password",
    "token",
    "api_key",
    "secret",
    "credential",
    "auth",
    "key",
    "private",
}


def log_command(f: Callable) -> Callable:
    """Decorator for logging CLI command lifecycle.

    Tracks command execution from start to completion, including:
    - Command invocation with unique ID
    - Sanitized arguments (secrets redacted)
    - Execution duration
    - Success/failure status

    Args:
        f: CLI command function to wrap

    Returns:
        Wrapped function with logging

    Example:
        @click.command()
        @click.option("--podcast-id", type=str)
        @log_command
        def refresh(podcast_id: str):
            # Command implementation
            pass
    """

    @wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Generate unique command ID for tracking this CLI command invocation
        command_id = str(uuid.uuid4())[:8]

        # Sanitize arguments (redact passwords, API keys, etc.)
        sanitized_kwargs = _sanitize_arguments(kwargs)

        # Bind command context for correlation using structlog's context variables.
        # These variables are automatically included in ALL log messages during
        # command execution, including:
        # - Service layer calls
        # - Core processor logs (download, transcribe, etc.)
        # - Database operations
        # This enables tracking CLI command lifecycle and debugging failures.
        structlog.contextvars.bind_contextvars(
            command_id=command_id,
            command_name=f.__name__,
        )

        # Log command start
        start_time = time.time()
        logger.info("cli_command_started", arguments=sanitized_kwargs)

        try:
            # Execute command
            result = f(*args, **kwargs)

            # Log successful completion
            duration_s = time.time() - start_time
            logger.info(
                "cli_command_completed",
                duration_s=round(duration_s, 2),
                success=True,
            )

            return result

        except Exception as e:
            # Log command failure
            duration_s = time.time() - start_time
            logger.error(
                "cli_command_failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_s=round(duration_s, 2),
                exc_info=True,
            )
            raise

        finally:
            # Clear command context
            structlog.contextvars.clear_contextvars()

    return wrapper


def _sanitize_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Sanitize command arguments by redacting sensitive values.

    Args:
        kwargs: Dictionary of command arguments

    Returns:
        Sanitized dictionary with secrets redacted
    """
    sanitized = {}

    for key, value in kwargs.items():
        # Check if key contains sensitive information
        if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
            sanitized[key] = "***REDACTED***"
        else:
            # Keep non-sensitive values
            sanitized[key] = value

    return sanitized
