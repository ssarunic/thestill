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

"""Temporary stdio adapter for MCP logging.

This module provides a bridge for logging MCP operations over stdio transport.
This is a temporary solution until the MCP server migrates to HTTP/SSE transport.

IMPORTANT: All logs go to stderr (stdout is reserved for MCP protocol communication).
"""

import asyncio
import time
import uuid
from functools import wraps
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


def log_mcp_stdio(f: Callable) -> Callable:
    """Decorator for logging MCP stdio operations (supports both sync and async).

    This is a temporary adapter until HTTP transport migration.
    All logs go to stderr (stdout reserved for MCP protocol).

    Args:
        f: Function to wrap (typically MCP tool or resource handler)

    Returns:
        Wrapped function with logging

    Example:
        @log_mcp_stdio
        async def get_episode_data(episode_id: str):
            return {"episode_id": episode_id, "title": "..."}
    """

    # Check if function is async
    if asyncio.iscoroutinefunction(f):

        @wraps(f)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Generate request ID
            request_id = str(uuid.uuid4())[:8]

            # Bind stdio context
            structlog.contextvars.bind_contextvars(
                request_id=request_id,
                transport="stdio",
                mcp_method=f.__name__,
            )

            # Log request start
            start_time = time.time()
            logger.debug("mcp_stdio_request", method=f.__name__)

            try:
                # Execute function
                result = await f(*args, **kwargs)

                # Log completion
                duration_ms = (time.time() - start_time) * 1000
                logger.debug(
                    "mcp_stdio_completed",
                    method=f.__name__,
                    duration_ms=round(duration_ms, 2),
                )

                return result

            except Exception as e:
                # Log failure with error categorization
                duration_ms = (time.time() - start_time) * 1000

                # Categorize error based on exception type and message
                error_type = _categorize_stdio_error(e)

                logger.error(
                    "mcp_stdio_failed",
                    method=f.__name__,
                    error_type=error_type,
                    error=str(e),
                    duration_ms=round(duration_ms, 2),
                    exc_info=True,
                )
                raise

            finally:
                # Clear context
                structlog.contextvars.clear_contextvars()

        return async_wrapper

    else:

        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Generate request ID
            request_id = str(uuid.uuid4())[:8]

            # Bind stdio context
            structlog.contextvars.bind_contextvars(
                request_id=request_id,
                transport="stdio",
                mcp_method=f.__name__,
            )

            # Log request start
            start_time = time.time()
            logger.debug("mcp_stdio_request", method=f.__name__)

            try:
                # Execute function
                result = f(*args, **kwargs)

                # Log completion
                duration_ms = (time.time() - start_time) * 1000
                logger.debug(
                    "mcp_stdio_completed",
                    method=f.__name__,
                    duration_ms=round(duration_ms, 2),
                )

                return result

            except Exception as e:
                # Log failure with error categorization
                duration_ms = (time.time() - start_time) * 1000

                # Categorize error based on exception type and message
                error_type = _categorize_stdio_error(e)

                logger.error(
                    "mcp_stdio_failed",
                    method=f.__name__,
                    error_type=error_type,
                    error=str(e),
                    duration_ms=round(duration_ms, 2),
                    exc_info=True,
                )
                raise

            finally:
                # Clear context
                structlog.contextvars.clear_contextvars()

        return wrapper


def _categorize_stdio_error(exception: Exception) -> str:
    """Categorize stdio transport errors.

    Since stdio doesn't have HTTP status codes, we categorize
    based on exception type and message content.

    Args:
        exception: The caught exception

    Returns:
        Error category string
    """
    error_msg = str(exception).lower()
    exception_type = type(exception).__name__

    # Protocol errors (invalid MCP requests)
    if "invalid" in error_msg or "malformed" in error_msg or "protocol" in error_msg:
        return "protocol_error"

    # Resource not found
    if "not found" in error_msg or exception_type in ("FileNotFoundError", "KeyError"):
        return "resource_not_found"

    # Validation errors
    if "validation" in error_msg or exception_type == "ValidationError":
        return "validation_error"

    # Tool execution errors
    if exception_type in ("RuntimeError", "OSError", "IOError"):
        return "tool_execution_error"

    # Unknown error
    return "unknown_error"
