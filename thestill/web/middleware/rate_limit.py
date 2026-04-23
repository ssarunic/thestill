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
In-process rate limiter (spec #25, item 2.3).

A tiny sliding-window limiter with per-key buckets. Used on auth and
webhook endpoints to blunt brute-force and webhook-spam, and on MCP
mutation tools to cap cost-DoS against cloud LLM / transcription
APIs.

Why not slowapi / Redis?  We run as a single process in single-user
deployments; a shared in-memory dict is sufficient. If the app ever
scales out, swap the implementation behind :func:`check` without
touching callers.  Limits are intentionally conservative; tune via
env vars when the real workload justifies it.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional

from fastapi import HTTPException, Request, status
from structlog import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimit:
    """A ``max_events`` per ``window_seconds`` sliding window."""

    max_events: int
    window_seconds: int


# Sensible defaults; every value is overridable via the matching env var
# so operators can relax or tighten in place without a code change.
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


AUTH_LIMIT = RateLimit(
    max_events=_int_env("RATE_LIMIT_AUTH_MAX", 10),
    window_seconds=_int_env("RATE_LIMIT_AUTH_WINDOW_SECONDS", 60),
)
WEBHOOK_LIMIT = RateLimit(
    max_events=_int_env("RATE_LIMIT_WEBHOOK_MAX", 60),
    window_seconds=_int_env("RATE_LIMIT_WEBHOOK_WINDOW_SECONDS", 60),
)
MCP_MUTATION_LIMIT = RateLimit(
    max_events=_int_env("RATE_LIMIT_MCP_MUTATION_MAX", 30),
    window_seconds=_int_env("RATE_LIMIT_MCP_MUTATION_WINDOW_SECONDS", 60),
)


class _SlidingWindow:
    """Thread-safe, key-scoped sliding window counter."""

    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: RateLimit) -> bool:
        now = time.monotonic()
        cutoff = now - limit.window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit.max_events:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        """Test helper."""
        with self._lock:
            self._buckets.clear()


_LIMITER = _SlidingWindow()


def _client_key(request: Request, prefix: str) -> str:
    """Derive a per-IP key for HTTP endpoints."""
    # Prefer the direct socket peer. When behind a trusted proxy, the
    # LoggingMiddleware / TrustedHost logic should have rewritten this
    # before the limiter runs. We intentionally do NOT consult
    # X-Forwarded-For here — an attacker can spoof it and evade limits.
    peer = request.client.host if request.client else "unknown"
    return f"{prefix}:{peer}"


def rate_limit_dependency(
    limit: RateLimit,
    prefix: str,
) -> Callable[[Request], None]:
    """
    Produce a FastAPI ``Depends``-compatible callable that applies
    ``limit`` to every request, keyed by client IP + ``prefix``.
    """

    def _dep(request: Request) -> None:
        key = _client_key(request, prefix)
        if not _LIMITER.allow(key, limit):
            logger.warning(
                "rate_limit_exceeded",
                key=key,
                max_events=limit.max_events,
                window_seconds=limit.window_seconds,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again shortly.",
                headers={"Retry-After": str(limit.window_seconds)},
            )

    return _dep


class RateLimitExceeded(Exception):
    """Raised by :func:`enforce_mcp_mutation_quota` when the quota is exhausted."""


def enforce_mcp_mutation_quota(tool_name: str, session_key: Optional[str] = None) -> None:
    """
    Guard an MCP mutation tool invocation. Combines the tool name with
    an optional session key so a single client cannot burn the budget
    for everyone else. Call at the top of the tool handler.
    """
    key = f"mcp:{session_key or 'default'}:{tool_name}"
    if not _LIMITER.allow(key, MCP_MUTATION_LIMIT):
        logger.warning(
            "mcp_mutation_quota_exceeded",
            tool_name=tool_name,
            session_key=session_key,
            max_events=MCP_MUTATION_LIMIT.max_events,
            window_seconds=MCP_MUTATION_LIMIT.window_seconds,
        )
        raise RateLimitExceeded(
            f"MCP tool {tool_name!r} has exceeded its rate limit "
            f"({MCP_MUTATION_LIMIT.max_events}/{MCP_MUTATION_LIMIT.window_seconds}s)."
        )


def reset_for_testing() -> None:
    """Flush all buckets — call from test fixtures, never production."""
    _LIMITER.reset()


__all__ = [
    "AUTH_LIMIT",
    "WEBHOOK_LIMIT",
    "MCP_MUTATION_LIMIT",
    "RateLimit",
    "RateLimitExceeded",
    "enforce_mcp_mutation_quota",
    "rate_limit_dependency",
    "reset_for_testing",
]
