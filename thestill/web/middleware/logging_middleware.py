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

"""HTTP request/response logging middleware for FastAPI."""

import time
import uuid
from typing import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses.

    This middleware:
    - Generates a unique request_id for each request
    - Logs request start with method, endpoint, and client IP
    - Logs request completion with status code and duration
    - Adds X-Request-ID header to responses
    - Binds request context for correlation with task logs

    Example:
        from fastapi import FastAPI
        from thestill.web.middleware import LoggingMiddleware

        app = FastAPI()
        app.add_middleware(LoggingMiddleware)
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process HTTP request with logging.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/route handler

        Returns:
            HTTP response with X-Request-ID header
        """
        # Generate unique request ID for tracking this request across all layers
        request_id = str(uuid.uuid4())[:8]

        # Bind request context for correlation using structlog's context variables.
        # These context variables are automatically included in ALL log messages
        # within this request's async context, including logs from:
        # - Route handlers
        # - Service layer functions
        # - Task creation (if request triggers background work)
        # This enables end-to-end request tracing without manual propagation.
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            endpoint=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )

        # Log request start
        start_time = time.time()
        logger.info("http_request_started", query_params=dict(request.query_params))

        try:
            # Process request
            response = await call_next(request)

            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000

            # Log completion with appropriate level
            log_level = "info" if response.status_code < 400 else "warning" if response.status_code < 500 else "error"
            log_method = getattr(logger, log_level)
            log_method(
                "http_request_completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            # Log unexpected errors
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                "http_request_failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2),
                exc_info=True,
            )
            raise

        finally:
            # Clear request context
            structlog.contextvars.clear_contextvars()
