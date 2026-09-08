"""Spec #78 Phase 2 — /api/me/mcp-token routes."""

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.repositories.sqlite_mcp_token_repository import SqliteMcpTokenRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.mcp_token_service import McpTokenService
from thestill.web.dependencies import get_app_state, require_auth
from thestill.web.routes import api_me_mcp_token

from .auth_harness import ADMIN_USER, PLAIN_USER

ROUTE = "/api/me/mcp-token"


def _client(
    tmp_path,
    *,
    current_user: User | None,
    multi_user=True,
    enabled=True,
    public_base_url="",
    ttl_days=90,
    base_url="https://testserver",
):
    db = str(tmp_path / "t.db")
    SqlitePodcastRepository(db_path=db)
    users = SqliteUserRepository(db_path=db)
    for u in (ADMIN_USER, PLAIN_USER):
        users.save(u)
    service = McpTokenService(SqliteMcpTokenRepository(db_path=db), ttl_days=ttl_days)

    state = MagicMock()
    state.config.multi_user = multi_user
    state.config.mcp_http_enabled = enabled
    state.config.public_base_url = public_base_url
    state.auth_service.get_current_user.return_value = current_user
    state.mcp_token_service = service

    app = FastAPI()
    app.include_router(api_me_mcp_token.router, prefix=ROUTE, dependencies=[Depends(require_auth)])
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app, raise_server_exceptions=False, base_url=base_url), service


@pytest.fixture(autouse=True)
def _uuid_users(monkeypatch):
    # The SQLite users table enforces 36-char ids; the harness users are short.
    monkeypatch.setattr(ADMIN_USER, "id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    monkeypatch.setattr(PLAIN_USER, "id", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class TestGate:
    def test_anonymous_is_401(self, tmp_path):
        client, _ = _client(tmp_path, current_user=None)
        assert client.get(ROUTE).status_code == 401
        assert client.post(ROUTE, json={"scopes": []}).status_code == 401
        assert client.delete(ROUTE).status_code == 401

    def test_plain_user_passes_the_gate(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER)
        assert client.get(ROUTE).status_code == 200


class TestLifecycle:
    def test_none_then_create_then_active(self, tmp_path):
        client, service = _client(tmp_path, current_user=PLAIN_USER, public_base_url="https://pods.example.com")
        assert client.get(ROUTE).json()["state"] == "none"

        created = client.post(ROUTE, json={"scopes": ["follows"]})
        assert created.status_code == 201
        body = created.json()
        assert body["url"].startswith("https://pods.example.com/mcp/")
        plaintext = body["url"].rsplit("/", 1)[1]
        assert len(plaintext) == 64
        assert body["scopes"] == ["read", "follows"]
        assert body["expires_at"] is not None

        shown = client.get(ROUTE).json()
        assert shown["state"] == "active"
        assert shown["prefix"] == plaintext[:6]
        assert shown["scopes"] == ["read", "follows"]
        # The plaintext never comes back from GET.
        assert plaintext not in created.request.url.path and plaintext not in str(shown)
        assert service.resolve_active(plaintext) is not None

    def test_rotate_kills_old_and_revoke_ends_it(self, tmp_path):
        client, service = _client(tmp_path, current_user=PLAIN_USER)
        first = client.post(ROUTE, json={"scopes": []}).json()["url"].rsplit("/", 1)[1]
        second = client.post(ROUTE, json={"scopes": []}).json()["url"].rsplit("/", 1)[1]
        assert service.resolve_active(first) is None
        assert service.resolve_active(second) is not None

        assert client.delete(ROUTE).status_code == 204
        assert service.resolve_active(second) is None
        assert client.get(ROUTE).json()["state"] == "revoked"
        assert client.delete(ROUTE).status_code == 204  # idempotent

    def test_ttl_zero_yields_no_expiry(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, ttl_days=0)
        assert client.post(ROUTE, json={"scopes": []}).json()["expires_at"] is None

    def test_disabled_server_refuses_mint_but_reports_state(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, enabled=False)
        body = client.get(ROUTE).json()
        assert (body["enabled"], body["state"]) == (False, "none")
        assert client.post(ROUTE, json={"scopes": []}).status_code == 400


class TestScopePolicy:
    def test_pipeline_dropped_for_non_admin(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER)
        assert client.post(ROUTE, json={"scopes": ["pipeline"]}).json()["scopes"] == ["read"]

    def test_pipeline_honoured_for_admin(self, tmp_path):
        client, _ = _client(tmp_path, current_user=ADMIN_USER)
        assert client.post(ROUTE, json={"scopes": ["pipeline"]}).json()["scopes"] == ["read", "pipeline"]

    def test_single_user_mode_counts_as_admin(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, multi_user=False)
        assert client.post(ROUTE, json={"scopes": ["pipeline"]}).json()["scopes"] == ["read", "pipeline"]

    def test_falls_back_to_request_base_url(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER)
        assert client.post(ROUTE, json={"scopes": []}).json()["url"].startswith("https://testserver/mcp/")


class TestSecureOriginOnly:
    """The token rides in the URL: only hand one out for an HTTPS (or
    loopback) origin, so a plain-http self-host cannot mint a URL that
    leaks in transit."""

    def test_plain_http_non_local_is_refused(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, base_url="http://pods.internal")
        response = client.post(ROUTE, json={"scopes": []})
        assert response.status_code == 400
        assert "HTTPS" in response.json()["detail"]

    def test_localhost_over_http_is_allowed(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, base_url="http://localhost:8000")
        assert client.post(ROUTE, json={"scopes": []}).status_code == 201

    def test_public_base_url_https_wins_over_proxy_http(self, tmp_path):
        client, _ = _client(
            tmp_path, current_user=PLAIN_USER, base_url="http://10.0.0.5", public_base_url="https://pods.example.com"
        )
        body = client.post(ROUTE, json={"scopes": []}).json()
        assert body["url"].startswith("https://pods.example.com/mcp/")

    def test_public_base_url_http_is_refused(self, tmp_path):
        client, _ = _client(tmp_path, current_user=PLAIN_USER, public_base_url="http://pods.example.com")
        assert client.post(ROUTE, json={"scopes": []}).status_code == 400
