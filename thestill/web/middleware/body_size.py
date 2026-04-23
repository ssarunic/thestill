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

"""
Request body size cap (spec #25 item 3.7).

Starlette does not cap request bodies by default, so an attacker can
upload a multi-gigabyte payload and keep it buffered in memory while
the handler tries to parse it. This middleware rejects any POST/PUT/
PATCH whose advertised ``Content-Length`` exceeds the per-route cap
with a 413 Payload Too Large.

Caps are chosen per-URL-prefix: webhooks get a tight cap (they should
be sub-MiB JSON bodies), everything else gets a moderate cap. Reverse
proxies should STILL enforce their own cap — this is the application-
layer backstop, not the only defence.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from structlog import get_logger

logger = get_logger(__name__)

# Body reads only apply to request methods that have bodies.
_METHODS_WITH_BODY = {"POST", "PUT", "PATCH"}


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured cap."""

    def __init__(
        self,
        app,
        *,
        default_limit: int,
        route_limits: Iterable[Tuple[str, int]] = (),
    ) -> None:
        """
        Args:
            default_limit: Fallback cap, in bytes, for any route not covered
                by ``route_limits``.
            route_limits: ``(path_prefix, limit_bytes)`` pairs. The first
                match (longest-prefix-first) wins. Pass e.g.
                ``[("/webhook/", 1 * 1024 * 1024)]`` to give webhooks a
                tight cap independent of the default.
        """
        super().__init__(app)
        # Sort by prefix length descending so more-specific prefixes match
        # first regardless of call order.
        self._route_limits: list = sorted(
            route_limits, key=lambda pair: len(pair[0]), reverse=True
        )
        self._default_limit = int(default_limit)

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self._route_limits:
            if path.startswith(prefix):
                return limit
        return self._default_limit

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.method not in _METHODS_WITH_BODY:
            return await call_next(request)

        limit = self._limit_for(request.url.path)
        length_header = request.headers.get("content-length")
        if length_header is not None:
            try:
                advertised = int(length_header)
            except ValueError:
                logger.warning(
                    "body_size_content_length_not_int",
                    header=length_header,
                    path=request.url.path,
                )
                # Raising HTTPException from a BaseHTTPMiddleware does NOT
                # convert to an HTTP response (that's a FastAPI-route-only
                # mechanism). Return a JSONResponse directly.
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
            if advertised > limit:
                logger.warning(
                    "body_size_cap_exceeded",
                    advertised=advertised,
                    cap=limit,
                    path=request.url.path,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Payload exceeds the {limit}-byte limit for this endpoint."
                    },
                )
        # A missing Content-Length on POST/PUT/PATCH is unusual but legal
        # (chunked transfer-encoding). Enforcing a streaming cap here is
        # possible but complicates the middleware; the reverse proxy in
        # production (spec #05) is expected to cap chunked uploads.

        return await call_next(request)


__all__ = ["BodySizeLimitMiddleware"]
