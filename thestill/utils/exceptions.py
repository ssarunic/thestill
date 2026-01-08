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
Custom exception classes for Thestill application.

This module defines application-specific exceptions that allow
selective error handling without catching system exceptions like
KeyboardInterrupt or SystemExit.

Example:
    try:
        process_podcast()
    except ThestillError as e:
        # Handle application errors
        logger.error(f"Application error: {e}")
    except KeyboardInterrupt:
        # System interrupts are not caught
        logger.info("Interrupted by user")
"""


class ThestillError(Exception):
    """
    Base exception for all Thestill application errors.

    Use this as the base class for all application-specific exceptions,
    or raise it directly for general application errors. This allows
    catching application errors without catching system exceptions.

    Attributes:
        message: Human-readable error message
        context: Optional dict of additional error context (url, path, etc.)

    Example:
        # Raise with message only
        raise ThestillError("Failed to process episode")

        # Raise with context
        raise ThestillError(
            "Failed to parse feed",
            url="https://example.com/feed.xml",
            feed_type="rss"
        )

        # Catch application errors
        try:
            process_podcast()
        except ThestillError as e:
            logger.error(f"Error: {e.message}")
            if e.context:
                logger.debug(f"Context: {e.context}")

        # Chain with original exception
        try:
            parse_feed(url)
        except Exception as e:
            raise ThestillError(f"Feed parsing failed: {url}") from e
    """

    def __init__(self, message: str, **context):
        """
        Initialize ThestillError.

        Args:
            message: Human-readable error message
            **context: Optional keyword arguments for error context
                      (e.g., url, path, episode_id, provider)
        """
        super().__init__(message)
        self.message = message
        self.context = context if context else {}

    def __str__(self):
        """Return string representation of error."""
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message

    def __repr__(self):
        """Return detailed representation for debugging."""
        if self.context:
            return f"ThestillError(message={self.message!r}, context={self.context!r})"
        return f"ThestillError(message={self.message!r})"


class TranscriptCleaningError(ThestillError):
    """
    Exception raised when transcript cleaning fails or is severely degraded.

    This is raised when:
    - More than 50% of chunks fail to process in Phase 1
    - LLM responses are consistently malformed
    - Critical processing steps cannot complete

    Example:
        raise TranscriptCleaningError(
            "Phase 1 failed: 3/4 chunks failed to process",
            chunks_failed=3,
            chunks_total=4,
            episode_id="abc123"
        )
    """

    pass


class TransientError(ThestillError):
    """
    Exception for transient errors that may succeed on retry.

    Use this for temporary failures that are likely to resolve:
    - Network timeouts
    - HTTP 502, 503, 504 (server temporarily unavailable)
    - HTTP 429 (rate limited)
    - Connection reset errors
    - LLM rate limits
    - Database lock errors

    The task worker will schedule automatic retries with exponential backoff
    when this exception is raised.

    Example:
        raise TransientError(
            "API rate limit exceeded",
            status_code=429,
            retry_after=60
        )
    """

    pass


class FatalError(ThestillError):
    """
    Exception for fatal errors that will never succeed on retry.

    Use this for permanent failures that require manual intervention:
    - HTTP 404 (resource not found)
    - HTTP 401, 403 (authentication/permission errors)
    - Corrupt audio files
    - Unsupported file formats
    - Invalid configuration
    - Data integrity errors (episode not found in database)

    The task worker will move tasks to the Dead Letter Queue (DLQ)
    when this exception is raised.

    Example:
        raise FatalError(
            "Audio file is corrupt and cannot be processed",
            file_path="/path/to/audio.mp3",
            error_details="Invalid MP3 header"
        )
    """

    pass


__all__ = ["ThestillError", "TranscriptCleaningError", "TransientError", "FatalError"]
