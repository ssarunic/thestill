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

"""HTTP middleware for MCP server (SSE/HTTP transport).

This module implements HTTP-first logging for MCP servers using HTTP/SSE transport.
This is the primary design for future MCP server implementations.

Current MCP server uses stdio transport - see stdio_adapter.py for the temporary bridge.
"""

import time
import uuid
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class MCPLoggingMiddleware(BaseHTTPMiddleware):
    """HTTP middleware for MCP server logging (SSE/HTTP transport).

    This middleware is designed for HTTP-based MCP servers and provides:
    - Request ID generation and correlation
    - MCP method/tool invocation tracking
    - Error categorization via HTTP status codes
    - Performance metrics (duration)
    - Cloud-native observability integration

    Status Code Mapping:
        - 200-399: Success
        - 400: Protocol error (malformed MCP request)
        - 404: Resource not found
        - 500+: Tool execution error

    Example:
        from fastapi import FastAPI
        from thestill.mcp.middleware import MCPLoggingMiddleware

        app = FastAPI()
        app.add_middleware(MCPLoggingMiddleware)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process MCP HTTP request with logging.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/route handler

        Returns:
            HTTP response with X-Request-ID header
        """
        # Generate unique request ID for tracking this MCP tool invocation
        request_id = str(uuid.uuid4())[:8]

        # Extract MCP method from request
        mcp_method = await self._extract_mcp_method(request)

        # Bind MCP context for correlation using structlog's context variables.
        # These variables are automatically included in ALL log messages during
        # this MCP tool invocation, including:
        # - MCP tool implementation logs
        # - Service layer calls
        # - Database queries
        # This enables tracking MCP tool usage patterns and performance.
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            mcp_method=mcp_method,
            transport="http",
        )

        # Log request start
        start_time = time.time()
        logger.info("mcp_request_started")

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Log completion with error categorization
            log_level = "info" if response.status_code < 400 else "error"
            error_category = self._categorize_error(response.status_code)

            logger.log(
                log_level,
                "mcp_request_completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                error_category=error_category,
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            # Log unexpected errors
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "mcp_request_failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2),
                exc_info=True,
            )
            raise

        finally:
            # Clear MCP context
            structlog.contextvars.clear_contextvars()

    async def _extract_mcp_method(self, request: Request) -> str:
        """Extract MCP method from request.

        Args:
            request: HTTP request

        Returns:
            MCP method string (e.g., "tools/call", "resources/read")
        """
        # For SSE transport, method is in the URL path
        path = request.url.path
        if "/tools/" in path:
            return "tools/call"
        elif "/resources/" in path:
            return "resources/read"
        elif "/prompts/" in path:
            return "prompts/get"

        # For JSON-RPC style requests, parse body
        try:
            if request.method == "POST":
                # Read body without consuming the stream
                body = await request.body()
                # Reset stream for downstream handlers
                request._body = body

                # Try to parse as JSON-RPC
                import json

                data = json.loads(body.decode("utf-8"))
                return data.get("method", "unknown")
        except Exception:
            pass

        return "unknown"

    def _categorize_error(self, status_code: int) -> str | None:
        """Map HTTP status code to MCP error category.

        Args:
            status_code: HTTP status code

        Returns:
            Error category string or None for success
        """
        if status_code < 400:
            return None
        elif status_code == 400:
            return "protocol_error"  # Malformed MCP request
        elif status_code == 404:
            return "resource_not_found"
        elif status_code >= 500:
            return "tool_execution_error"
        return "unknown_error"
