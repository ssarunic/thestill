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
"""What an MCP client may be told about a failure.

Exception text is not an API. ``str(exc)`` from a database driver, an HTTP
client or the filesystem carries SQL fragments, parameter values, hostnames
and paths, and over the remote connector it goes to whoever holds a token.
The web app already refuses to relay it (``_generic_exception_handler`` in
``web/app.py``); this module is the same rule for the MCP surface.

Every place that turns an exception into client-visible text goes through
:func:`public_error_message`: the full detail is logged under a short
reference id, and the client gets either a message that was *written for
it* or a generic one carrying that id.
"""

from __future__ import annotations

import uuid
from typing import Any, Tuple, Type

import structlog

logger = structlog.get_logger(__name__)


class McpUserError(ValueError):
    """A failure whose message was written for the MCP client.

    Subclasses ``ValueError`` because the resource and URI code raised bare
    ``ValueError`` for these before, and callers still catch that.
    """


def _authored_exception_types() -> Tuple[Type[BaseException], ...]:
    # Imported lazily: identity/utils/rate_limit sit on import paths that
    # reach back into this package.
    from ..web.middleware.rate_limit import RateLimitExceeded
    from .identity import NotAuthenticatedError, ScopeError

    # NumericIdentifierRefused is an McpUserError.
    return (McpUserError, ScopeError, NotAuthenticatedError, RateLimitExceeded)


def public_error_message(
    exc: BaseException,
    *,
    operation: str,
    expose_detail: bool = False,
    also_authored: Tuple[Type[BaseException], ...] = (),
    **log_context: Any,
) -> str:
    """Log ``exc`` in full and return the text the client may see.

    Args:
        exc: The exception being reported.
        operation: Tool name or resource operation, for the log line.
        expose_detail: Development only — relay the exception text, as the
            web app's handler does when ``ENVIRONMENT=development``.
        also_authored: Extra exception types whose message is known to be
            written for the client *at this call site* (e.g. a service
            documented to raise ``ValueError("Podcast not found: …")``).
        **log_context: Entity ids worth having on the log line.
    """
    if isinstance(exc, _authored_exception_types() + tuple(also_authored)):
        return str(exc)

    error_ref = uuid.uuid4().hex[:8]
    logger.error(
        "mcp_internal_error",
        error_ref=error_ref,
        operation=operation,
        error_type=type(exc).__name__,
        error=str(exc),
        exc_info=exc,
        **log_context,
    )
    if expose_detail:
        return f"{type(exc).__name__}: {exc} (ref {error_ref})"
    return f"Internal error (ref {error_ref}). The details were logged on the server."
