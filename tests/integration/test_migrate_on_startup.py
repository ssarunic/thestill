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

"""``run_alembic_upgrade`` (spec #66 MIGRATE_ON_STARTUP): idempotent,
serialized across concurrent callers via the shared bootstrap advisory
lock, and convergent with an ``ensure_schema``-bootstrapped database
(every revision is IF-NOT-EXISTS idempotent by design — see
migrations/env.py). Skipped without a reachable ``TEST_DATABASE_URL``."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from thestill.repositories.postgres_schema import run_alembic_upgrade

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
    reason="Postgres not reachable — set TEST_DATABASE_URL to run the migration tests",
)


@pytest.fixture(autouse=True)
def _pin_database_url(monkeypatch):
    """migrations/env.py resolves DATABASE_URL from the environment first;
    pin it to the test DSN so a developer's real .env can never leak in and
    migrate the wrong database."""
    monkeypatch.setenv("DATABASE_URL", PG_DSN)


def _current_revision() -> str:
    import psycopg

    with psycopg.connect(PG_DSN) as conn:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else ""


def _head_revision() -> str:
    from pathlib import Path

    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    import thestill.repositories.postgres_schema as schema_mod

    cfg = AlembicConfig()
    cfg.set_main_option(
        "script_location",
        str(Path(schema_mod.__file__).resolve().parent.parent / "migrations"),
    )
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_upgrade_reaches_head_and_reruns_as_noop():
    run_alembic_upgrade(PG_DSN)
    head = _head_revision()
    assert _current_revision() == head

    run_alembic_upgrade(PG_DSN)  # second run: fast no-op, must not raise
    assert _current_revision() == head


def test_concurrent_upgrades_serialize_without_deadlock():
    workers = 3
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_alembic_upgrade, PG_DSN) for _ in range(workers)]
        for future in futures:
            future.result(timeout=120)  # raises on deadlock/timeout
    assert _current_revision() == _head_revision()
