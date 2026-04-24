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
Security response headers (spec #25 item 3.1).

Applies a conservative set of browser-enforced defences to every
response served by the FastAPI app:

* ``Content-Security-Policy`` — limits where scripts / styles / images
  can come from. The SPA is served same-origin so ``'self'`` covers
  everything except podcast artwork (external ``https:`` images) and
  React's injected styles (``'unsafe-inline'`` for ``style-src`` is
  pragmatic for now; tighten if the frontend moves to CSS-in-JS with
  nonce support).
* ``Strict-Transport-Security`` — only emitted in production so local
  http dev isn't accidentally pinned to a TLS cert the dev box
  doesn't have.
* ``X-Content-Type-Options: nosniff`` — stops browsers from guessing
  that a text/plain transcript is actually HTML.
* ``X-Frame-Options: DENY`` + CSP ``frame-ancestors 'none'`` —
  clickjacking defence. Both are set because older browsers only
  understand the former.
* ``Referrer-Policy: strict-origin-when-cross-origin`` — keeps full
  URLs off third-party logs.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "media-src 'self' https:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply defence-in-depth headers to every HTTP response."""

    def __init__(self, app, *, is_production: bool) -> None:
        super().__init__(app)
        self._is_production = is_production

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        response: Response = await call_next(request)
        headers = response.headers
        # setdefault-style: don't overwrite a header the route set deliberately.
        if "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = _CSP
        if "X-Content-Type-Options" not in headers:
            headers["X-Content-Type-Options"] = "nosniff"
        if "X-Frame-Options" not in headers:
            headers["X-Frame-Options"] = "DENY"
        if "Referrer-Policy" not in headers:
            headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if self._is_production and "Strict-Transport-Security" not in headers:
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


__all__ = ["SecurityHeadersMiddleware"]
