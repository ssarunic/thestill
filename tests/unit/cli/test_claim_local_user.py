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

"""CliRunner tests for ``thestill claim-local-user`` (spec #64)."""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services.auth_service import DEFAULT_USER_EMAIL


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Tmp DB wired into the CLI via env (same pattern as test_backfill_inbox)."""
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))

    db_path = storage / "podcasts.db"
    SqlitePodcastRepository(db_path=str(db_path))
    return str(db_path)


def _seed(db_path, *, local_follows=1, real_email="real@example.com"):
    conn = sqlite3.connect(db_path)
    try:
        local_id, real_id = str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute("INSERT INTO users (id, email, name) VALUES (?, ?, ?)", (local_id, DEFAULT_USER_EMAIL, "Local"))
        conn.execute("INSERT INTO users (id, email, name) VALUES (?, ?, ?)", (real_id, real_email, "Real"))
        for _ in range(local_follows):
            pid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title, slug) VALUES (?, ?, ?, ?)",
                (pid, f"https://example.com/{pid[:8]}.xml", "P", f"p-{pid[:8]}"),
            )
            conn.execute(
                "INSERT INTO podcast_followers (id, user_id, podcast_id) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), local_id, pid),
            )
        conn.commit()
        return local_id, real_id
    finally:
        conn.close()


def _follower_count(db_path, user_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM podcast_followers WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def _local_exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM users WHERE email = ?", (DEFAULT_USER_EMAIL,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_requires_exactly_one_mode(cli_db):
    runner = CliRunner()
    assert runner.invoke(main, ["claim-local-user"]).exit_code == 1
    assert runner.invoke(main, ["claim-local-user", "--to", "x@y.z", "--discard"]).exit_code == 1


def test_claim_transfers_to_real_user(cli_db):
    _, real_id = _seed(cli_db, local_follows=2)
    result = CliRunner().invoke(main, ["claim-local-user", "--to", "real@example.com"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "2 follows" in result.output
    assert _follower_count(cli_db, real_id) == 2
    assert not _local_exists(cli_db)


def test_claim_dry_run_moves_nothing(cli_db):
    local_id, real_id = _seed(cli_db, local_follows=2)
    result = CliRunner().invoke(
        main, ["claim-local-user", "--to", "real@example.com", "--dry-run"], catch_exceptions=False
    )
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert _follower_count(cli_db, local_id) == 2
    assert _follower_count(cli_db, real_id) == 0
    assert _local_exists(cli_db)


def test_claim_unknown_target_fails(cli_db):
    _seed(cli_db)
    result = CliRunner().invoke(main, ["claim-local-user", "--to", "nobody@example.com"])
    assert result.exit_code == 1
    assert "No user found" in result.output


def test_discard_deletes_without_transfer(cli_db):
    local_id, real_id = _seed(cli_db, local_follows=3)
    result = CliRunner().invoke(main, ["claim-local-user", "--discard"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "discarded" in result.output
    assert not _local_exists(cli_db)
    assert _follower_count(cli_db, local_id) == 0  # cascaded
    assert _follower_count(cli_db, real_id) == 0  # nothing transferred


def test_noop_when_local_gone(cli_db):
    _seed(cli_db)
    assert CliRunner().invoke(main, ["claim-local-user", "--discard"], catch_exceptions=False).exit_code == 0
    result = CliRunner().invoke(main, ["claim-local-user", "--discard"], catch_exceptions=False)
    assert result.exit_code == 0
    assert "nothing to do" in result.output
