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

"""``factory.ping`` — the backend-resolved DB round-trip behind
``/health/ready`` (spec #66). Raises on failure, returns None on success."""

from __future__ import annotations

import os
import sqlite3

import pytest

from thestill.repositories.factory import ping
from thestill.utils.config import Config

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


class TestSqlitePing:
    def test_succeeds_against_tmp_database(self, tmp_path):
        config = Config(storage_path=tmp_path)
        ping(config)  # must not raise

    def test_raises_when_database_directory_missing(self, tmp_path):
        config = Config(
            storage_path=tmp_path,
            database_path=str(tmp_path / "does-not-exist" / "podcasts.db"),
        )
        with pytest.raises(sqlite3.OperationalError):
            ping(config)


class TestPostgresPing:
    @pytest.mark.skipif(
        not _pg_reachable(PG_DSN),
        reason="Postgres not reachable — set TEST_DATABASE_URL to run",
    )
    def test_succeeds_against_test_database(self, tmp_path):
        config = Config(storage_path=tmp_path, database_url=PG_DSN)
        ping(config)  # must not raise

    def test_raises_against_unreachable_server(self, tmp_path):
        pytest.importorskip("psycopg")
        config = Config(
            storage_path=tmp_path,
            # Reserved TEST-NET address — connect_timeout=3 bounds the wait.
            database_url="postgresql://x:y@192.0.2.1:5432/nope",
        )
        with pytest.raises(Exception):
            ping(config)
