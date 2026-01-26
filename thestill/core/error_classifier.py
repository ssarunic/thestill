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
Error classification utility for task retry logic.

This module provides utilities to classify exceptions as transient (retryable)
or fatal (should go to DLQ). It's used by task handlers to wrap exceptions
appropriately so the TaskWorker can handle them correctly.

Usage:
    from thestill.core.error_classifier import classify_and_raise

    try:
        response = requests.get(url)
        response.raise_for_status()
    except requests.RequestException as e:
        classify_and_raise(e, context="downloading audio")
"""

import re
from typing import Optional

from structlog import get_logger

from thestill.utils.exceptions import FatalError, TransientError

logger = get_logger(__name__)

# HTTP status codes that indicate transient errors (should retry)
TRANSIENT_HTTP_CODES = {
    408,  # Request Timeout
    429,  # Too Many Requests (rate limited)
    500,  # Internal Server Error (sometimes recoverable)
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
    522,  # Connection Timed Out (Cloudflare)
    524,  # A Timeout Occurred (Cloudflare)
}

# HTTP status codes that indicate fatal errors (no point retrying)
FATAL_HTTP_CODES = {
    400,  # Bad Request
    401,  # Unauthorized
    403,  # Forbidden
    404,  # Not Found
    405,  # Method Not Allowed
    410,  # Gone
    415,  # Unsupported Media Type
    422,  # Unprocessable Entity
    451,  # Unavailable For Legal Reasons
}

# Error message patterns that indicate transient errors
TRANSIENT_PATTERNS = [
    r"timeout",
    r"timed out",
    r"connection reset",
    r"connection refused",
    r"connection aborted",
    r"temporary failure",
    r"temporarily unavailable",
    r"rate limit",
    r"too many requests",
    r"server busy",
    r"try again",
    r"retry",
    r"overloaded",
    r"capacity",
    r"throttl",
    r"network.*(error|unreachable)",
    r"dns.*fail",
    r"socket.*error",
    r"ssl.*error",
    r"database.*locked",
    r"sqlite.*locked",
    r"broken pipe",
]

# Error message patterns that indicate fatal errors
FATAL_PATTERNS = [
    r"not found",
    r"does not exist",
    r"file not found",
    r"no such file",
    r"permission denied",
    r"access denied",
    r"unauthorized",
    r"forbidden",
    r"invalid.*format",
    r"corrupt",
    r"malformed",
    r"unsupported.*format",
    r"unsupported.*codec",
    r"invalid.*audio",
    r"invalid.*video",
    r"cannot decode",
    r"decoding failed",
    r"episode not found",
    r"podcast not found",
    r"configuration error",
    r"missing.*api.*key",
    r"invalid.*api.*key",
    r"authentication failed",
]


def is_transient_error(exception: Exception) -> bool:
    """
    Check if an exception represents a transient (retryable) error.

    Args:
        exception: The exception to classify

    Returns:
        True if the error is likely transient and should be retried
    """
    error_str = str(exception).lower()
    exception_type = type(exception).__name__.lower()

    # Check for HTTP response errors with status codes
    status_code = _extract_http_status_code(exception)
    if status_code:
        if status_code in TRANSIENT_HTTP_CODES:
            return True
        if status_code in FATAL_HTTP_CODES:
            return False

    # Check for transient patterns in error message
    for pattern in TRANSIENT_PATTERNS:
        if re.search(pattern, error_str, re.IGNORECASE):
            return True

    # Check exception type names
    transient_exception_types = [
        "timeout",
        "connectionerror",
        "networkerror",
        "temporaryerror",
        "retriableerror",
    ]
    for transient_type in transient_exception_types:
        if transient_type in exception_type:
            return True

    return False


def is_fatal_error(exception: Exception) -> bool:
    """
    Check if an exception represents a fatal (non-retryable) error.

    Args:
        exception: The exception to classify

    Returns:
        True if the error is fatal and should go to DLQ
    """
    error_str = str(exception).lower()

    # Check for HTTP response errors with status codes
    status_code = _extract_http_status_code(exception)
    if status_code and status_code in FATAL_HTTP_CODES:
        return True

    # Check for fatal patterns in error message
    for pattern in FATAL_PATTERNS:
        if re.search(pattern, error_str, re.IGNORECASE):
            return True

    # FileNotFoundError is always fatal
    if isinstance(exception, FileNotFoundError):
        return True

    return False


def _extract_http_status_code(exception: Exception) -> Optional[int]:
    """
    Extract HTTP status code from an exception if available.

    Args:
        exception: The exception to check

    Returns:
        HTTP status code if found, None otherwise
    """
    # Check for requests.HTTPError
    if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
        return exception.response.status_code

    # Check for httpx.HTTPStatusError
    if hasattr(exception, "response") and hasattr(exception.response, "status_code"):
        return exception.response.status_code

    # Check if it's stored as an attribute
    if hasattr(exception, "status_code"):
        return exception.status_code

    # Try to extract from error message (common patterns)
    error_str = str(exception)
    patterns = [
        r"(\d{3})\s+(client|server)\s+error",
        r"status[_\s]?code[:\s]+(\d{3})",
        r"http[:\s]+(\d{3})",
        r"error\s+(\d{3})",
    ]
    for pattern in patterns:
        match = re.search(pattern, error_str, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue

    return None


def classify_and_raise(
    exception: Exception,
    context: str = "",
    default_transient: bool = True,
) -> None:
    """
    Classify an exception and re-raise as TransientError or FatalError.

    This is the main entry point for error classification. It examines the
    exception and raises the appropriate error type for the TaskWorker to handle.

    Args:
        exception: The original exception
        context: Optional context string (e.g., "downloading audio", "transcribing")
        default_transient: If error can't be classified, treat as transient (True)
                          or fatal (False). Default True for safer retry behavior.

    Raises:
        TransientError: If the error is transient and should be retried
        FatalError: If the error is fatal and should go to DLQ
    """
    # Already classified - re-raise as-is
    if isinstance(exception, (TransientError, FatalError)):
        raise exception

    error_msg = str(exception)
    if context:
        error_msg = f"{context}: {error_msg}"

    # Check for fatal first (more specific)
    if is_fatal_error(exception):
        logger.debug(f"Classified as fatal: {error_msg}")
        raise FatalError(error_msg) from exception

    # Check for transient
    if is_transient_error(exception):
        logger.debug(f"Classified as transient: {error_msg}")
        raise TransientError(error_msg) from exception

    # Unknown error - use default
    if default_transient:
        logger.debug(f"Unknown error, defaulting to transient: {error_msg}")
        raise TransientError(error_msg) from exception
    else:
        logger.debug(f"Unknown error, defaulting to fatal: {error_msg}")
        raise FatalError(error_msg) from exception


def wrap_as_transient(exception: Exception, context: str = "") -> TransientError:
    """
    Wrap an exception as a TransientError.

    Use this when you know an error should be retried regardless of pattern matching.

    Args:
        exception: The original exception
        context: Optional context string

    Returns:
        TransientError wrapping the original exception
    """
    error_msg = str(exception)
    if context:
        error_msg = f"{context}: {error_msg}"
    return TransientError(error_msg)


def wrap_as_fatal(exception: Exception, context: str = "") -> FatalError:
    """
    Wrap an exception as a FatalError.

    Use this when you know an error should go to DLQ regardless of pattern matching.

    Args:
        exception: The original exception
        context: Optional context string

    Returns:
        FatalError wrapping the original exception
    """
    error_msg = str(exception)
    if context:
        error_msg = f"{context}: {error_msg}"
    return FatalError(error_msg)
