"""Spec #78 Phase 2 — McpTokenService lifecycle rules."""

from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.mcp_token import McpToken
from thestill.models.user import User
from thestill.repositories.sqlite_mcp_token_repository import SqliteMcpTokenRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.mcp_token_service import McpTokenMint, McpTokenService

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
USER_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "t.db")
    SqlitePodcastRepository(db_path=db)
    SqliteUserRepository(db_path=db).save(User(id=USER_ID, email="u1@example.com"))
    return SqliteMcpTokenRepository(db_path=db)


@pytest.fixture
def service(repo):
    return McpTokenService(repo, ttl_days=90)


class TestScopePolicy:
    def test_read_is_always_present(self):
        assert McpTokenService.normalize_scopes([], is_admin=False) == ("read",)

    def test_follows_kept_when_requested(self):
        assert McpTokenService.normalize_scopes(["follows"], is_admin=False) == ("read", "follows")

    def test_pipeline_dropped_for_non_admin_not_an_error(self):
        assert McpTokenService.normalize_scopes(["follows", "pipeline"], is_admin=False) == ("read", "follows")

    def test_pipeline_kept_for_admin(self):
        assert McpTokenService.normalize_scopes(["pipeline"], is_admin=True) == ("read", "pipeline")

    def test_unknown_scopes_ignored(self):
        assert McpTokenService.normalize_scopes(["root", "read"], is_admin=True) == ("read",)


class TestMint:
    def test_plaintext_returned_once_and_only_hash_stored(self, service, repo):
        mint = service.create_or_rotate(USER_ID, requested_scopes=["follows"], is_admin=False, now=NOW)
        assert isinstance(mint, McpTokenMint)
        assert len(mint.token) == 64
        row = repo.get(USER_ID)
        assert row.token_hash == McpTokenService.hash_token(mint.token)
        assert mint.token not in (row.token_hash, row.token_prefix)
        assert row.token_prefix == mint.token[:6]
        assert row.scopes == ("read", "follows")
        assert row.expires_at == NOW + timedelta(days=90)

    def test_negative_ttl_is_refused(self, repo):
        with pytest.raises(ValueError, match=">= 0"):
            McpTokenService(repo, ttl_days=-1)

    def test_ttl_zero_means_no_expiry(self, repo):
        service = McpTokenService(repo, ttl_days=0)
        mint = service.create_or_rotate(USER_ID, requested_scopes=[], is_admin=False, now=NOW)
        assert mint.expires_at is None
        assert repo.get(USER_ID).expires_at is None

    def test_rotate_kills_old_plaintext(self, service):
        first = service.create_or_rotate(USER_ID, requested_scopes=[], is_admin=False, now=NOW)
        second = service.create_or_rotate(USER_ID, requested_scopes=[], is_admin=False, now=NOW + timedelta(days=1))
        assert service.resolve_active(first.token, now=NOW + timedelta(days=2)) is None
        assert service.resolve_active(second.token, now=NOW + timedelta(days=2)) is not None


class TestResolveAndRevoke:
    def test_resolve_active_respects_expiry(self, service):
        mint = service.create_or_rotate(USER_ID, requested_scopes=[], is_admin=False, now=NOW)
        assert service.resolve_active(mint.token, now=NOW + timedelta(days=89)) is not None
        assert service.resolve_active(mint.token, now=NOW + timedelta(days=91)) is None

    def test_revoke(self, service):
        mint = service.create_or_rotate(USER_ID, requested_scopes=[], is_admin=False, now=NOW)
        assert service.revoke(USER_ID, now=NOW) is True
        assert service.resolve_active(mint.token, now=NOW) is None
        assert service.revoke(USER_ID, now=NOW) is False


class TestState:
    def _token(self, **kw):
        base = dict(user_id=USER_ID, token_hash="h", token_prefix="p", created_at=NOW)
        base.update(kw)
        return McpToken(**base)

    def test_states(self):
        s = McpTokenService.state_of
        assert s(self._token(expires_at=None), now=NOW) == "active"
        assert s(self._token(expires_at=NOW + timedelta(days=30)), now=NOW) == "active"
        assert s(self._token(expires_at=NOW + timedelta(days=13)), now=NOW) == "expiring"
        assert s(self._token(expires_at=NOW - timedelta(seconds=1)), now=NOW) == "expired"
        assert s(self._token(expires_at=NOW + timedelta(days=30), revoked_at=NOW), now=NOW) == "revoked"
