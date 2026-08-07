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

"""Spec #69 Phase 2 — keep synchronous work off the event loop.

The repositories are synchronous psycopg3/sqlite3, so FastAPI must run
route handlers that touch them in its threadpool — which only happens for
sync ``def`` routes. An ``async def`` route that calls sync repository,
file, or LLM code holds the single event loop for its full duration and
stalls every concurrent request in the process.

Two layers of protection:

1. **AST guard** — the exact set of ``async def`` route handlers is pinned.
   A new async route fails the test until it is added to
   ``ALLOWED_ASYNC_ROUTES``, which should only happen for handlers that
   genuinely await (SSE streams, ``request.body()``, OAuth flows,
   ``run_in_threadpool`` wraps) and never call sync DB/file/LLM code
   directly on the loop.
2. **Behavioral gate** — a deliberately slow *sync* route must not delay a
   concurrent ``/health`` request. If someone converts a hot route back to
   ``async def``, the slow handler blocks the loop and this test fails on
   latency.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
from fastapi import Depends, FastAPI

import thestill.web.routes as routes_pkg
from thestill.web.dependencies import get_app_state

ROUTES_DIR = Path(routes_pkg.__file__).parent

# (module, function) pairs that are allowed to be ``async def``. Every entry
# must have a reason; anything else in thestill/web/routes must be sync.
ALLOWED_ASYNC_ROUTES = {
    # SSE stream: awaits an asyncio.Queue in a generator.
    ("api_commands", "stream_task_progress"),
    # OAuth flows await the async auth_service methods.
    ("auth", "auth_status"),
    ("auth", "google_callback"),
    # Liveness probe: no I/O of any kind, so serving it on the loop keeps
    # it responsive even when the threadpool is saturated.
    ("health", "health_check"),
    # Awaits request.body(), then threadpools the sync processing.
    ("webhooks", "elevenlabs_webhook"),
}

_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _route_handlers(path: Path):
    """Yield ``(name, is_async, has_await)`` for each route handler in file."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr in _HTTP_METHODS
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id.endswith("router")
            ):
                has_await = any(isinstance(sub, ast.Await) for sub in ast.walk(node))
                yield node.name, isinstance(node, ast.AsyncFunctionDef), has_await
                break


def test_async_routes_are_exactly_the_allowlist():
    found_async = set()
    for path in sorted(ROUTES_DIR.glob("*.py")):
        module = path.stem
        for name, is_async, has_await in _route_handlers(path):
            if is_async:
                found_async.add((module, name))

    unexpected = found_async - ALLOWED_ASYNC_ROUTES
    assert not unexpected, (
        f"New async route handler(s) {sorted(unexpected)} — sync repositories "
        "must run in the threadpool. Declare the route as plain 'def' (or wrap "
        "sync calls in run_in_threadpool and add the route to "
        "ALLOWED_ASYNC_ROUTES with a reason)."
    )

    stale = ALLOWED_ASYNC_ROUTES - found_async
    assert not stale, f"Stale ALLOWED_ASYNC_ROUTES entries: {sorted(stale)}"


def test_allowlisted_async_routes_actually_await():
    """Except the no-I/O liveness probe, an async route that never awaits
    has no reason to be async — it only risks landing sync work on the loop."""
    no_await_ok = {("health", "health_check")}
    for path in sorted(ROUTES_DIR.glob("*.py")):
        module = path.stem
        for name, is_async, has_await in _route_handlers(path):
            if is_async and (module, name) not in no_await_ok:
                assert has_await, f"{module}.{name} is async but never awaits"


def test_slow_sync_route_does_not_block_event_loop():
    """Behavioral gate: /health must answer while a slow sync handler runs.

    ``get_status`` is a converted hot route; its stats_service is stubbed to
    block for 1s (standing in for a slow query / file scan). Because the
    route is sync ``def`` FastAPI runs it in the threadpool, so the async
    ``/health`` handler on the event loop must answer immediately. If the
    route regresses to ``async def``, the sleep holds the loop and /health
    takes ~1s — far over the assertion bound.
    """
    from thestill.web.routes import api_status, health

    app = FastAPI()
    app.include_router(health.router)
    app.include_router(api_status.router, prefix="/api/status")

    state = MagicMock()

    def _slow_stats():
        time.sleep(1.0)
        raise RuntimeError("stats not needed for this test")

    state.stats_service.get_stats.side_effect = _slow_stats
    app.dependency_overrides[get_app_state] = lambda: state

    async def _run() -> float:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            slow = asyncio.create_task(client.get("/api/status"))
            # Let the slow request enter its handler (threadpool) first.
            await asyncio.sleep(0.1)
            t0 = time.monotonic()
            health_resp = await client.get("/health")
            elapsed = time.monotonic() - t0
            assert health_resp.status_code == 200
            slow_resp = await slow
            assert slow_resp.status_code == 500  # stub raised after sleeping
            return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed < 0.5, (
        f"/health took {elapsed:.3f}s while a slow sync route was running — "
        "the event loop appears to be blocked (did a route regress to async def?)"
    )
