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
    def test_succeeds_against_bootstrapped_database(self, tmp_path):
        from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

        config = Config(storage_path=tmp_path)
        SqlitePodcastRepository(db_path=str(config.database_path))  # creates schema
        ping(config)  # must not raise

    def test_raises_when_database_file_missing(self, tmp_path):
        # mode=rw must NOT create the file: a deleted database is not ready.
        config = Config(storage_path=tmp_path)
        with pytest.raises(sqlite3.OperationalError):
            ping(config)
        assert not (tmp_path / "podcasts.db").exists()

    def test_raises_when_database_is_an_empty_shell(self, tmp_path):
        config = Config(storage_path=tmp_path)
        sqlite3.connect(str(config.database_path)).close()  # file, no schema
        with pytest.raises(RuntimeError, match="podcasts table"):
            ping(config)

    def test_path_with_uri_delimiters_is_escaped(self, tmp_path):
        from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository

        # Unescaped, the '?' would start the URI query string: mode=rw gets
        # dropped and sqlite opens/creates the truncated sibling path.
        tricky = tmp_path / "podcasts?tenant=one.db"
        config = Config(storage_path=tmp_path, database_path=str(tricky))
        SqlitePodcastRepository(db_path=str(tricky))
        ping(config)  # must probe the real file, not a truncated sibling
        assert not (tmp_path / "podcasts").exists()

    def test_missing_path_with_uri_delimiters_still_refuses_create(self, tmp_path):
        config = Config(storage_path=tmp_path, database_path=str(tmp_path / "gone?x=1.db"))
        with pytest.raises(sqlite3.OperationalError):
            ping(config)
        # Neither the file nor a ?-truncated sibling may have been created
        # (Config itself pre-creates storage subdirectories — ignore those).
        assert list(tmp_path.glob("gone*")) == []

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
