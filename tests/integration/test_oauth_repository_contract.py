"""Dual-backend contract suite for the OAuth repository (spec #109).

Same shape as ``test_mcp_token_repository_contract.py``: every behaviour is
asserted identically on SQLite and PostgreSQL. Postgres cases skip (not
fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.oauth import OAuthClientRecord, OAuthPending, OAuthTokenRecord
from thestill.models.user import User
from thestill.repositories.sqlite_oauth_repository import SqliteOAuthRepository
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

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
CLIENT = "https://claude.ai/oauth/mcp-oauth-client-metadata"


@pytest.fixture(params=["sqlite", "postgres"])
def repos(request, tmp_path):
    """Yield ``(oauth_repo, user_repo)`` on a clean database for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the users + oauth_* DDL
        yield SqliteOAuthRepository(db_path=db), SqliteUserRepository(db_path=db)
        return

    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    import psycopg

    from thestill.repositories.postgres_oauth_repository import PostgresOAuthRepository
    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.repositories.postgres_user_repository import PostgresUserRepository

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, oauth_clients, oauth_pending, oauth_grants, oauth_codes, oauth_tokens CASCADE")
    yield PostgresOAuthRepository(PG_DSN), PostgresUserRepository(PG_DSN)


@pytest.fixture
def user(repos):
    _, user_repo = repos
    return user_repo.save(User(email="ada@example.com", name="Ada"))


def _pending(id_hash: str = "p1", scopes=("read", "offline_access"), **overrides) -> OAuthPending:
    base = dict(
        id_hash=id_hash,
        client_id=CLIENT,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_explicit=True,
        state="xyz",
        scopes=tuple(scopes),
        code_challenge="c" * 43,
        resource="https://thestill.me/mcp",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    base.update(overrides)
    return OAuthPending(**base)


def _approve(repo, user_id, *, id_hash="p1", scopes=("read", "offline_access"), code="code1", now=NOW):
    repo.save_pending(_pending(id_hash, scopes))
    return repo.approve(
        id_hash,
        now=now,
        user_id=user_id,
        new_grant_id=str(uuid.uuid4()),
        client_name="Claude",
        redirect_host="claude.ai",
        scopes=scopes,
        code_hash=code,
        code_expires_at=now + timedelta(seconds=60),
    )


def _tokens(grant_id: str, access: str, refresh: str, scopes=("read", "offline_access"), now=NOW):
    return [
        OAuthTokenRecord(
            token_hash=access,
            grant_id=grant_id,
            client_id=CLIENT,
            kind="access",
            scopes=scopes,
            resource="https://thestill.me/mcp",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
        OAuthTokenRecord(
            token_hash=refresh,
            grant_id=grant_id,
            client_id=CLIENT,
            kind="refresh",
            scopes=scopes,
            resource="https://thestill.me/mcp",
            created_at=now,
            expires_at=now + timedelta(days=90),
        ),
    ]


# -- clients ------------------------------------------------------------------


def test_client_round_trip_and_upsert_keeps_created_at(repos):
    repo, _ = repos
    meta = {"client_id": CLIENT, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude"}
    repo.save_client(
        OAuthClientRecord(
            client_id=CLIENT, source="cimd", client_name="Claude", metadata=meta, created_at=NOW, fetched_at=NOW
        )
    )
    later = NOW + timedelta(hours=1)
    repo.save_client(
        OAuthClientRecord(
            client_id=CLIENT, source="cimd", client_name="Claude 2", metadata=meta, created_at=later, fetched_at=later
        )
    )
    got = repo.get_client(CLIENT)
    assert got.client_name == "Claude 2"
    assert got.metadata == meta
    assert got.created_at == NOW and got.fetched_at == later
    assert repo.get_client("nope") is None


# -- pending ------------------------------------------------------------------


def test_pending_expiry_and_delete(repos):
    repo, _ = repos
    repo.save_pending(_pending())
    assert repo.get_pending("p1", now=NOW).scopes == ("read", "offline_access")
    assert repo.get_pending("p1", now=NOW + timedelta(minutes=10)) is None
    assert repo.delete_pending("p1") is True
    assert repo.delete_pending("p1") is False


def test_approve_consumes_pending_once(repos, user):
    repo, _ = repos
    result = _approve(repo, user.id)
    assert result is not None
    grant, code = result
    assert grant.user_id == user.id and grant.scopes == ("read", "offline_access")
    assert code.grant_id == grant.id and code.redirect_uri.endswith("auth_callback")
    again = repo.approve(
        "p1",
        now=NOW,
        user_id=user.id,
        new_grant_id=str(uuid.uuid4()),
        client_name="x",
        redirect_host="claude.ai",
        scopes=("read",),
        code_hash="code2",
        code_expires_at=NOW + timedelta(seconds=60),
    )
    assert again is None
    assert repo.get_code("code2") is None


def test_approve_expired_pending_refused(repos, user):
    repo, _ = repos
    repo.save_pending(_pending())
    late = NOW + timedelta(minutes=11)
    assert (
        repo.approve(
            "p1",
            now=late,
            user_id=user.id,
            new_grant_id=str(uuid.uuid4()),
            client_name=None,
            redirect_host="claude.ai",
            scopes=("read",),
            code_hash="c",
            code_expires_at=late,
        )
        is None
    )


def test_same_scopes_keep_grant_different_scopes_replace_it(repos, user):
    repo, _ = repos
    first, _ = _approve(repo, user.id, id_hash="p1", code="c1")
    same, _ = _approve(repo, user.id, id_hash="p2", code="c2")
    assert same.id == first.id

    wider, _ = _approve(repo, user.id, id_hash="p3", scopes=("read", "follows", "offline_access"), code="c3")
    assert wider.id != first.id
    old = repo.get_grant(first.id)
    assert old.revoked_at == NOW and old.revoked_reason == "replaced"
    assert [g.id for g in repo.list_active_grants(user.id)] == [wider.id]


# -- codes and tokens ------------------------------------------------------------


def test_redeem_code_once_then_refused(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    assert repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1")) is True
    assert repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a2", "r2")) is False
    code, _ = repo.get_code("code1")
    assert code.used_at == NOW
    assert repo.get_token("a2") is None


def test_redeem_refused_when_expired_or_grant_revoked(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    assert repo.redeem_code("code1", now=NOW + timedelta(seconds=61), tokens=_tokens(grant.id, "a", "r")) is False
    grant2, _ = _approve(repo, user.id, id_hash="p2", scopes=("read",), code="code2")
    # code1's grant was replaced by grant2: unredeemed code1 is dead.
    assert repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a", "r")) is False
    assert repo.redeem_code("code2", now=NOW, tokens=_tokens(grant2.id, "a", "r", scopes=("read",))) is True


def test_access_token_lookup_follows_grant_and_expiry(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    token, g = repo.find_active_access("a1", now=NOW)
    assert token.scopes == ("read", "offline_access") and g.user_id == user.id
    assert repo.find_active_access("r1", now=NOW) is None  # refresh is not an access token
    assert repo.find_active_access("a1", now=NOW + timedelta(hours=1)) is None
    assert repo.revoke_grant(grant.id, now=NOW, reason="user") is True
    assert repo.find_active_access("a1", now=NOW) is None


def test_token_scopes_are_their_own_not_the_grants(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id, scopes=("read", "follows", "offline_access"))
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1", scopes=("read",)))
    token, g = repo.find_active_access("a1", now=NOW)
    assert token.scopes == ("read",) and g.scopes == ("read", "follows", "offline_access")


def test_rotate_refresh_compare_and_set(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    later = NOW + timedelta(minutes=50)
    assert repo.rotate_refresh("r1", now=later, tokens=_tokens(grant.id, "a2", "r2", now=later)) is True
    old, _ = repo.get_token("r1")
    assert old.replaced_by_hash == "r2"
    # The retired token cannot rotate again; nothing is written.
    assert repo.rotate_refresh("r1", now=later, tokens=_tokens(grant.id, "a3", "r3", now=later)) is False
    assert repo.get_token("a3") is None
    # An access token is not a refresh token.
    assert repo.rotate_refresh("a2", now=later, tokens=_tokens(grant.id, "a4", "r4", now=later)) is False


def test_rotate_refresh_has_one_winner_under_concurrency(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    results = []

    def attempt(i):
        results.append(repo.rotate_refresh("r1", now=NOW, tokens=_tokens(grant.id, f"ax{i}", f"rx{i}")))

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 5 + [True]


def test_rotate_refused_after_grant_revoked(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    repo.revoke_user_grants(user.id, now=NOW, reason="admin")
    assert repo.rotate_refresh("r1", now=NOW, tokens=_tokens(grant.id, "a2", "r2")) is False


# -- grants -------------------------------------------------------------------------


def test_revoke_grant_scoped_to_user(repos, user):
    repo, user_repo = repos
    other = user_repo.save(User(email="bob@example.com", name="Bob"))
    grant, _ = _approve(repo, user.id)
    assert repo.revoke_grant(grant.id, now=NOW, reason="user", user_id=other.id) is False
    assert repo.revoke_grant(grant.id, now=NOW, reason="user", user_id=user.id) is True
    assert repo.get_grant(grant.id).revoked_reason == "user"


def test_revoke_user_grants_counts(repos, user):
    repo, _ = repos
    _approve(repo, user.id)
    assert repo.revoke_user_grants(user.id, now=NOW, reason="admin") == 1
    assert repo.revoke_user_grants(user.id, now=NOW, reason="admin") == 0
    assert repo.list_active_grants(user.id) == []


def test_touch_grant_throttled(repos, user):
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    assert repo.touch_grant(grant.id, now=NOW, ip="1.1.1.1", min_interval_seconds=60) is True
    assert repo.touch_grant(grant.id, now=NOW + timedelta(seconds=30), ip="2.2.2.2", min_interval_seconds=60) is False
    got = repo.get_grant(grant.id)
    assert got.last_used_at == NOW and got.last_used_ip == "1.1.1.1"


def test_deleting_user_cascades(repos, user):
    repo, user_repo = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    user_repo.delete(user.id)
    assert repo.get_grant(grant.id) is None
    assert repo.get_token("a1") is None and repo.get_code("code1") is None


# -- housekeeping -------------------------------------------------------------------


def test_prune(repos, user):
    repo, _ = repos
    meta = {"client_id": "dcr-1", "redirect_uris": ["http://localhost/cb"]}
    repo.save_client(OAuthClientRecord(client_id="dcr-1", source="dcr", metadata=meta, created_at=NOW))
    repo.save_client(OAuthClientRecord(client_id=CLIENT, source="dcr", metadata=meta, created_at=NOW))
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    repo.rotate_refresh("r1", now=NOW, tokens=_tokens(grant.id, "a2", "r2"))
    repo.save_pending(_pending("stale"))

    later = NOW + timedelta(days=40)
    deleted = repo.prune(
        now=later, retired_before=later - timedelta(days=30), unused_clients_before=later - timedelta(days=7)
    )
    # stale pending, code1, a1 + a2 (expired), r1 (retired 40 days ago), dcr-1 (no grant).
    assert deleted == 6
    assert repo.get_token("r1") is None and repo.get_token("r2") is not None
    assert repo.get_client("dcr-1") is None and repo.get_client(CLIENT) is not None
    assert repo.get_grant(grant.id) is not None


def test_prune_keeps_a_retired_refresh_for_30_days_from_retirement(repos, user):
    """A refresh token idle for weeks and then rotated is still recognised as
    a replay for 30 days after the rotation, not after its issue."""
    repo, _ = repos
    grant, _ = _approve(repo, user.id)
    repo.redeem_code("code1", now=NOW, tokens=_tokens(grant.id, "a1", "r1"))
    rotated = NOW + timedelta(days=35)
    repo.rotate_refresh("r1", now=rotated, tokens=_tokens(grant.id, "a2", "r2", now=rotated))
    later = NOW + timedelta(days=40)
    repo.prune(now=later, retired_before=later - timedelta(days=30), unused_clients_before=later - timedelta(days=7))
    assert repo.get_token("r1") is not None
    much_later = rotated + timedelta(days=31)
    repo.prune(
        now=much_later,
        retired_before=much_later - timedelta(days=30),
        unused_clients_before=much_later - timedelta(days=7),
    )
    assert repo.get_token("r1") is None


def test_prune_drops_stale_metadata_documents_nobody_used(repos, user):
    repo, _ = repos
    meta = {"client_id": "https://x.example/doc", "redirect_uris": ["https://x.example/cb"]}
    repo.save_client(
        OAuthClientRecord(
            client_id="https://x.example/doc",
            source="cimd",
            metadata=meta,
            created_at=NOW,
            fresh_until=NOW + timedelta(hours=1),
        )
    )
    used = {**meta, "client_id": CLIENT}
    repo.save_client(OAuthClientRecord(client_id=CLIENT, source="cimd", metadata=used, created_at=NOW, fresh_until=NOW))
    _approve(repo, user.id)
    later = NOW + timedelta(days=8)
    repo.prune(now=later, retired_before=later - timedelta(days=30), unused_clients_before=later - timedelta(days=7))
    assert repo.get_client("https://x.example/doc") is None
    assert repo.get_client(CLIENT) is not None
