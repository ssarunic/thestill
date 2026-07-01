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

"""Dual-backend contract suite for the user repository (spec #44).

The same tests run against BOTH the SQLite and the PostgreSQL implementations
behind the ``UserRepository`` ABC. This is the fidelity guarantee spec #42
FM-5 demands: the Postgres port is exercised against a *real* Postgres, never a
mock, and every behaviour is asserted identically on both engines so a dialect
divergence fails the build.

The Postgres cases are skipped (not failed) when no server is reachable, so the
suite still passes on a SQLite-only CI runner. Point ``TEST_DATABASE_URL`` at a
Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_test \\
        ./venv/bin/python -m pytest tests/integration/test_user_repository_contract.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.user import User
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

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


PG_OK = _pg_reachable(PG_DSN)


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    """Yield a clean user repository for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        # SqlitePodcastRepository owns the users/revoked_tokens DDL.
        SqlitePodcastRepository(db_path=db)
        yield SqliteUserRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_user_repository import PostgresUserRepository

    r = PostgresUserRepository(PG_DSN)  # ensures schema
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, revoked_tokens")
    yield r


def _mk_user(**overrides) -> User:
    base = dict(email="ada@example.com", name="Ada", google_id="g-1", region="gb")
    base.update(overrides)
    return User(**base)


# ---------------------------------------------------------------------------
# CRUD + lookups
# ---------------------------------------------------------------------------
def test_save_then_get_by_id(repo):
    u = _mk_user()
    repo.save(u)
    got = repo.get_by_id(u.id)
    assert got is not None
    assert got.id == u.id
    assert got.email == "ada@example.com"
    assert got.name == "Ada"
    # tz-aware instant round-trips identically on both engines.
    assert got.created_at == u.created_at


def test_get_by_email_and_google_id(repo):
    u = _mk_user()
    repo.save(u)
    assert repo.get_by_email("ada@example.com").id == u.id
    assert repo.get_by_google_id("g-1").id == u.id


def test_missing_lookups_return_none(repo):
    assert repo.get_by_id("00000000-0000-0000-0000-000000000000") is None
    assert repo.get_by_email("nobody@example.com") is None
    assert repo.get_by_google_id("nope") is None


def test_upsert_updates_mutable_but_preserves_region_and_admin(repo):
    """ON CONFLICT(email): name/picture/google_id/last_login update; region,
    region_locked and is_admin are preserved (the dialect-sensitive path)."""
    first = _mk_user(name="Ada", region="gb", region_locked=True, is_admin=True)
    repo.save(first)
    # Same email, different mutable fields, and region/lock/admin that MUST NOT win.
    second = _mk_user(name="Ada Lovelace", region="us", region_locked=False, is_admin=False)
    repo.save(second)

    got = repo.get_by_email("ada@example.com")
    assert got.id == first.id  # identity stays with the original row
    assert got.name == "Ada Lovelace"  # mutable field updated
    assert got.region == "gb"  # preserved
    assert got.region_locked is True  # bool fidelity preserved
    assert got.is_admin is True  # preserved


def test_update_region_normalises_lowercase(repo):
    u = _mk_user(region=None, region_locked=False)
    repo.save(u)
    assert repo.update_region(u.id, "US", locked=True) is True
    got = repo.get_by_id(u.id)
    assert got.region == "us"
    assert got.region_locked is True
    # Missing user → False
    assert repo.update_region("00000000-0000-0000-0000-000000000000", "gb", locked=True) is False


def test_update_last_login(repo):
    u = _mk_user(last_login_at=None)
    repo.save(u)
    assert repo.update_last_login(u.id) is True
    got = repo.get_by_id(u.id)
    assert got.last_login_at is not None
    assert got.last_login_at.tzinfo is not None  # tz-aware on both engines
    assert repo.update_last_login("00000000-0000-0000-0000-000000000000") is False


def test_delete(repo):
    u = _mk_user()
    repo.save(u)
    assert repo.delete(u.id) is True
    assert repo.get_by_id(u.id) is None
    assert repo.delete(u.id) is False  # already gone


def test_region_locked_and_is_admin_false_roundtrip(repo):
    """Explicitly assert the False case survives (0/1 vs boolean divergence)."""
    u = _mk_user(region_locked=False, is_admin=False)
    repo.save(u)
    got = repo.get_by_id(u.id)
    assert got.region_locked is False
    assert got.is_admin is False


# ---------------------------------------------------------------------------
# JWT revocation deny-list
# ---------------------------------------------------------------------------
def test_revoke_and_check_token_is_idempotent(repo):
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    assert repo.is_token_revoked("jti-1") is False
    repo.revoke_token("jti-1", exp)
    assert repo.is_token_revoked("jti-1") is True
    # Re-revoking the same jti is a no-op, not a constraint error.
    repo.revoke_token("jti-1", exp)
    assert repo.is_token_revoked("jti-1") is True


def test_empty_jti_is_never_revoked(repo):
    repo.revoke_token("", datetime.now(timezone.utc) + timedelta(hours=1))
    assert repo.is_token_revoked("") is False


def test_prune_expired_revocations(repo):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.revoke_token("old", past)
    repo.revoke_token("fresh", future)
    pruned = repo.prune_expired_revocations()
    assert pruned == 1
    assert repo.is_token_revoked("old") is False
    assert repo.is_token_revoked("fresh") is True


# ---------------------------------------------------------------------------
# Backend factory (spec #44 Phase 0 selector)
# ---------------------------------------------------------------------------
def test_factory_selects_sqlite_by_default(tmp_path):
    from types import SimpleNamespace

    from thestill.repositories.factory import make_user_repository, uses_postgres

    db = str(tmp_path / "factory.db")
    SqlitePodcastRepository(db_path=db)
    cfg = SimpleNamespace(database_url="", database_path=db)
    assert uses_postgres(cfg) is False
    assert make_user_repository(cfg).__class__.__name__ == "SqliteUserRepository"


def test_factory_selects_postgres_when_url_set():
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL")
    from types import SimpleNamespace

    from thestill.repositories.factory import make_user_repository, uses_postgres

    cfg = SimpleNamespace(database_url=PG_DSN, database_path="")
    assert uses_postgres(cfg) is True
    assert make_user_repository(cfg).__class__.__name__ == "PostgresUserRepository"
