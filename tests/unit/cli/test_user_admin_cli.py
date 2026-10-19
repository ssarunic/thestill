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

"""CliRunner tests for ``thestill user`` / ``admin`` / ``settings`` (spec #104)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from thestill.cli import main
from thestill.models.podcast import Podcast
from thestill.models.user import PodcastFollower, User
from thestill.repositories.sqlite_audit_log_repository import SqliteAuditLogRepository
from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository


def _env(tmp_path, monkeypatch, *, multi_user: bool) -> str:
    storage = tmp_path / "data"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setenv("THESTILL_ENV_FILE", str(tmp_path / ".no-such-env"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 32)
    if multi_user:
        monkeypatch.setenv("MULTI_USER", "true")
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://thestill.test")
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    else:
        monkeypatch.setenv("MULTI_USER", "false")
    db = str(storage / "podcasts.db")
    SqlitePodcastRepository(db_path=db)
    return db


@pytest.fixture
def db(tmp_path, monkeypatch):
    db = _env(tmp_path, monkeypatch, multi_user=True)
    users = SqliteUserRepository(db)
    users.save(User(email="op@example.com", is_admin=True))
    users.save(User(email="spam@example.com"))
    podcasts = SqlitePodcastRepository(db_path=db)
    podcasts.save(Podcast(rss_url="https://example.com/a.xml", title="A", description=""))
    podcast = podcasts.get_by_url("https://example.com/a.xml")
    SqlitePodcastFollowerRepository(db).add(
        PodcastFollower(user_id=users.get_by_email("spam@example.com").id, podcast_id=podcast.id)
    )
    return db


def run(*args):
    return CliRunner().invoke(main, ["-q", *args])


def as_json(stdout: str):
    """The JSON document in ``stdout``. A repeat CLI run in one process can
    put a structlog line ahead of it, so start at the first brace."""
    return json.loads(stdout[stdout.index("{") :])


def test_list_and_show(db):
    result = run("user", "list", "--sort", "follows", "--json")
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert rows[0]["email"] == "spam@example.com" and rows[0]["follows"] == 1

    result = run("user", "show", "SPAM@example.com", "--json")
    assert result.exit_code == 0, result.output
    info = as_json(result.stdout)
    assert info["status"] == "active" and info["feeds_brought_in"] == 1


def test_ban_then_reinstate_is_audited_as_cli(db):
    result = run("user", "ban", "spam@example.com", "--reason", "script")
    assert result.exit_code == 0, result.output
    assert "1 follows moved aside" in result.output
    assert as_json(run("user", "show", "spam@example.com", "--json").stdout)["status"] == "banned"

    result = run("user", "reinstate", "spam@example.com")
    assert result.exit_code == 0, result.output
    assert "1 follows restored" in result.output

    entries = SqliteAuditLogRepository(db).list()
    assert [e.action for e in entries] == ["user.reinstate", "user.ban"]
    assert {e.actor for e in entries} == {"cli"}


def test_suspend_requires_a_reason_and_a_known_user(db):
    assert run("user", "suspend", "spam@example.com").exit_code == 2  # click: missing --reason
    result = run("user", "suspend", "ghost@example.com", "--reason", "x")
    assert result.exit_code == 1 and "No user matches" in result.output


def test_last_admin_cannot_be_revoked(db):
    result = run("admin", "revoke", "op@example.com")
    assert result.exit_code == 1 and "last active admin" in result.output
    assert run("admin", "grant", "spam@example.com").exit_code == 0
    assert run("admin", "revoke", "op@example.com").exit_code == 0


def test_settings_get_set_reset(db):
    result = run("settings", "set", "signup_mode", "closed")
    assert result.exit_code == 0, result.output
    payload = as_json(run("settings", "get", "signup_mode", "--json").stdout)
    assert payload["value"] == "closed" and payload["source"] == "runtime"

    assert run("settings", "set", "signup_allowlist", "a@x.io,@corp.io").exit_code == 0
    assert as_json(run("settings", "get", "signup_allowlist", "--json").stdout)["value"] == ["a@x.io", "@corp.io"]

    bad = run("settings", "set", "read_only", "maybe")
    assert bad.exit_code == 1 and "Invalid value" in bad.output
    assert run("settings", "set", "nope", "1").exit_code == 1

    assert run("settings", "reset", "signup_mode").exit_code == 0
    assert as_json(run("settings", "get", "signup_mode", "--json").stdout)["source"] == "env"


def test_user_commands_need_multi_user_mode(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, multi_user=False)
    result = run("user", "list")
    assert result.exit_code == 1 and "multi-user mode" in result.output
    # The global switches apply in single-user mode too.
    assert run("settings", "set", "read_only", "on").exit_code == 0
