"""Dual-backend contract suite for the MCP token repository (spec #78 Phase 2).

Same shape as ``test_user_repository_contract.py``: every behaviour is
asserted identically on SQLite and PostgreSQL. Postgres cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.mcp_token import McpToken
from thestill.models.user import User
from thestill.repositories.sqlite_mcp_token_repository import SqliteMcpTokenRepository
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

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(params=["sqlite", "postgres"])
def repos(request, tmp_path):
    """Yield ``(token_repo, user_repo)`` on a clean database for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the users + mcp_tokens DDL
        yield SqliteMcpTokenRepository(db_path=db), SqliteUserRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_mcp_token_repository import PostgresMcpTokenRepository
    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.repositories.postgres_user_repository import PostgresUserRepository

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, mcp_tokens CASCADE")
    yield PostgresMcpTokenRepository(PG_DSN), PostgresUserRepository(PG_DSN)


@pytest.fixture
def user(repos):
    _, user_repo = repos
    return user_repo.save(User(email="ada@example.com", name="Ada"))


def _token(user_id: str, **overrides) -> McpToken:
    base = dict(
        user_id=user_id,
        token_hash="a" * 64,
        token_prefix="abcdef",
        scopes=("read",),
        created_at=NOW,
        expires_at=NOW + timedelta(days=90),
    )
    base.update(overrides)
    return McpToken(**base)


def test_upsert_then_get_round_trips(repos, user):
    repo, _ = repos
    token = _token(user.id, scopes=("read", "follows"))
    repo.upsert(token)
    got = repo.get(user.id)
    assert got is not None
    assert got.token_hash == token.token_hash
    assert got.token_prefix == "abcdef"
    assert got.scopes == ("read", "follows")
    assert got.created_at == NOW
    assert got.expires_at == NOW + timedelta(days=90)
    assert got.last_used_at is None and got.last_used_ip is None and got.revoked_at is None


def test_get_unknown_user_is_none(repos, user):
    repo, _ = repos
    assert repo.get(user.id) is None


def test_find_active_matches_only_live_rows(repos, user):
    repo, _ = repos
    repo.upsert(_token(user.id))
    assert repo.find_active("a" * 64, now=NOW) is not None
    assert repo.find_active("b" * 64, now=NOW) is None
    # Expired: the predicate lives in SQL, so the row is invisible past expiry.
    assert repo.find_active("a" * 64, now=NOW + timedelta(days=91)) is None
    # No expiry at all (TTL disabled).
    repo.upsert(_token(user.id, expires_at=None))
    assert repo.find_active("a" * 64, now=NOW + timedelta(days=3650)) is not None


def test_rotate_replaces_row_in_place_and_kills_old_hash(repos, user):
    repo, _ = repos
    repo.upsert(_token(user.id, token_hash="a" * 64))
    repo.touch_last_used(user.id, now=NOW, ip="1.2.3.4", min_interval_seconds=60)
    repo.revoke(user.id, revoked_at=NOW)
    # Rotate: one upsert resets hash, revoked_at and last-use fields.
    repo.upsert(_token(user.id, token_hash="c" * 64, token_prefix="cccccc", created_at=NOW + timedelta(hours=1)))
    assert repo.find_active("a" * 64, now=NOW + timedelta(hours=2)) is None
    got = repo.find_active("c" * 64, now=NOW + timedelta(hours=2))
    assert got is not None
    assert got.revoked_at is None
    assert got.last_used_at is None and got.last_used_ip is None
    assert got.token_prefix == "cccccc"


def test_revoke_hides_row_from_find_active_but_get_still_sees_it(repos, user):
    repo, _ = repos
    repo.upsert(_token(user.id))
    assert repo.revoke(user.id, revoked_at=NOW) is True
    assert repo.revoke(user.id, revoked_at=NOW) is False  # already revoked
    assert repo.find_active("a" * 64, now=NOW) is None
    got = repo.get(user.id)
    assert got is not None and got.revoked_at == NOW


def test_touch_last_used_is_throttled_in_sql(repos, user):
    repo, _ = repos
    repo.upsert(_token(user.id))
    assert repo.touch_last_used(user.id, now=NOW, ip="1.1.1.1", min_interval_seconds=60) is True
    # 30 s later: inside the window, no write.
    assert (
        repo.touch_last_used(user.id, now=NOW + timedelta(seconds=30), ip="2.2.2.2", min_interval_seconds=60) is False
    )
    got = repo.get(user.id)
    assert got.last_used_at == NOW and got.last_used_ip == "1.1.1.1"
    # 60 s later: window elapsed, write happens.
    assert repo.touch_last_used(user.id, now=NOW + timedelta(seconds=60), ip="3.3.3.3", min_interval_seconds=60) is True
    got = repo.get(user.id)
    assert got.last_used_at == NOW + timedelta(seconds=60) and got.last_used_ip == "3.3.3.3"


def test_deleting_user_cascades(repos, user):
    repo, user_repo = repos
    repo.upsert(_token(user.id))
    user_repo.delete(user.id)
    assert repo.get(user.id) is None
