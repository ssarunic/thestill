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

"""Concurrent ``ensure_schema`` bootstraps must serialize, not deadlock.

2026-07-28 incident: two thestill-mcp processes started ~3 ms apart, each
ran the multi-statement DDL bootstrap in one transaction, and their
exclusive locks interleaved into a Postgres deadlock that killed the MCP
server at startup. ``ensure_schema`` now takes a transaction-scoped
advisory lock first, so concurrent bootstraps queue up instead.

Each thread opens its own connection (its own Postgres backend), which is
exactly the cross-process shape of the incident. Skipped without a
reachable ``TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from thestill.repositories.postgres_schema import ensure_schema

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(PG_DSN),
    reason="Postgres not reachable — set TEST_DATABASE_URL to run the schema bootstrap tests",
)


def test_concurrent_bootstraps_do_not_deadlock():
    workers = 4
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(ensure_schema, PG_DSN) for _ in range(workers)]
        # .result() re-raises — a deadlock surfaces as psycopg.errors.DeadlockDetected.
        for future in futures:
            future.result(timeout=120)


def test_bootstrap_is_idempotent():
    ensure_schema(PG_DSN)
    ensure_schema(PG_DSN)
