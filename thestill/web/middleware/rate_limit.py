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


# Once the bucket table grows past this size, each allow() call evicts any
# keys whose newest entry is older than the current window. Keeps the
# in-memory footprint bounded even under IP-rotation spam.
_SWEEP_TRIGGER = 1024


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
            # Opportunistic sweep: drop any other empty bucket we stumble
            # across so a long-running process doesn't accumulate one entry
            # per unique client IP forever.
            if len(self._buckets) > _SWEEP_TRIGGER:
                self._sweep(cutoff)
            return True

    def _sweep(self, cutoff: float) -> None:
        # Caller holds self._lock.
        stale = [k for k, b in self._buckets.items() if not b or b[-1] < cutoff]
        for k in stale:
            del self._buckets[k]

    def reset(self) -> None:
        """Test helper."""
        with self._lock:
            self._buckets.clear()


_LIMITER = _SlidingWindow()


def _resolve_client_ip(request: Request) -> str:
    """
    Identify the real client behind any trusted reverse proxy.

    spec #25 item 2.3 (post-review hardening): using ``request.client.host``
    alone collapses all users to the reverse-proxy IP in real deployments,
    which means one misbehaving client can lock everyone out. To avoid that
    AND the inverse problem (``X-Forwarded-For`` is attacker-spoofable), we
    only consult the forwarded header when the immediate peer is in
    ``Config.trusted_proxies``.

    Note on XFF semantics: clients append and proxies prepend. The
    left-most entry is the originating client; we walk from the right,
    discarding trusted-proxy hops, and take the first untrusted hop as
    the real client. This matches IETF RFC 7239's intent and defeats
    spoofing because an attacker adding a fake XFF entry can only
    pollute entries that appear BEFORE the last trusted hop.
    """
    peer = request.client.host if request.client else "unknown"
    trusted_proxies: Optional[set] = None
    try:
        config = getattr(request.app.state.app_state, "config", None)
        if config is not None:
            trusted_proxies = set(config.trusted_proxies or [])
    except AttributeError:
        trusted_proxies = None

    if not trusted_proxies or peer not in trusted_proxies:
        return peer

    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded:
        return peer

    # Walk right-to-left, skipping every trusted proxy hop.
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        if hop not in trusted_proxies:
            return hop
    # All hops were trusted proxies — fall back to the direct peer.
    return peer


def _client_key(request: Request, prefix: str) -> str:
    """Derive a per-real-client key for HTTP endpoints."""
    return f"{prefix}:{_resolve_client_ip(request)}"


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
