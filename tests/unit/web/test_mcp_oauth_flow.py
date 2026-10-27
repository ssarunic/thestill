"""Spec #109 — the MCP OAuth flow end to end over HTTP.

Real SDK routes, our provider and client adapter, the consent API, the
bearer guard and the session manager, on SQLite. Covers metadata, the 401
handshake, DCR and CIMD registration, PKCE, loopback redirects, the
two-authorization scope case, refresh rotation and reuse, revocation and
the personal URLs still working beside it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User, UserStatus
from thestill.repositories.factory import make_repositories
from thestill.services.mcp_token_service import McpTokenService
from thestill.services.oauth_cimd import FetchedDocument
from thestill.utils.config import Config
from thestill.web.dependencies import get_app_state, require_auth
from thestill.web.mcp_http import MOUNT_PATH, build_mcp_http
from thestill.web.mcp_oauth import build_mcp_oauth
from thestill.web.middleware import rate_limit
from thestill.web.routes import api_oauth

BASE = "http://localhost"
RESOURCE = f"{BASE}/mcp"
USER_A = "11111111-1111-4111-8111-111111111111"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
CIMD_URL = "https://claude.ai/oauth/claude-code-client-metadata"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    empty_env = tmp_path / ".env"
    empty_env.touch()
    monkeypatch.setenv("THESTILL_ENV_FILE", str(empty_env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rate_limit.reset_for_testing()
    yield
    rate_limit.reset_for_testing()


class Harness:
    def __init__(self, tmp_path):
        self.config = Config(
            storage_path=tmp_path,
            multi_user=True,
            jwt_secret_key="x" * 64,
            cookie_secure=False,
            mcp_http_enabled=True,
            mcp_oauth_enabled=True,
            public_base_url=BASE,
        )
        self.repos = make_repositories(self.config)
        self.user = self.repos.user.save(User(id=USER_A, email="a@example.com", is_admin=False))
        runtime = build_mcp_http(self.config, self.repos)
        self.cimd_doc = {
            "client_id": CIMD_URL,
            "client_name": "Claude Code",
            "redirect_uris": ["http://localhost/callback"],
        }
        self.service, routes = build_mcp_oauth(
            self.config,
            self.repos,
            runtime,
            cimd_fetch=lambda url: FetchedDocument(dict(self.cimd_doc), timedelta(hours=1)),
        )

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with runtime.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        app.router.routes.extend(routes)
        app.mount(MOUNT_PATH, runtime)
        app.include_router(api_oauth.pending_router, prefix="/api/oauth/pending", dependencies=[Depends(require_auth)])
        app.include_router(api_oauth.grants_router, prefix="/api/me/oauth-grants", dependencies=[Depends(require_auth)])
        state = SimpleNamespace(config=self.config, mcp_oauth_service=self.service)
        app.dependency_overrides[get_app_state] = lambda: state
        app.dependency_overrides[require_auth] = lambda: self.user
        self.app = app

    def client(self) -> TestClient:
        return TestClient(self.app, base_url=BASE)

    # -- flow helpers ---------------------------------------------------------------

    @staticmethod
    def register(c, **extra):
        body = {
            "redirect_uris": ["http://localhost:9999/cb"],
            "client_name": "Inspector",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **extra,
        }
        r = c.post("/register", json=body)
        assert r.status_code == 201, r.text
        return r.json()

    @staticmethod
    def pkce():
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return verifier, challenge

    @staticmethod
    def authorize(c, client_id, redirect_uri, challenge, scope="read offline_access", **extra):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st",
            "resource": RESOURCE,
            **({"scope": scope} if scope is not None else {}),
            **extra,
        }
        return c.get("/authorize", params=params, follow_redirects=False)

    def consent(self, c, client_id, redirect_uri, *, scope="read offline_access", ticked=("read",)):
        verifier, challenge = self.pkce()
        r = self.authorize(c, client_id, redirect_uri, challenge, scope=scope)
        assert r.status_code == 302, r.text
        location = r.headers["location"]
        assert location.startswith(f"{BASE}/oauth/consent#request=")
        request_id = location.split("#request=", 1)[1]
        approved = c.post(f"/api/oauth/pending/{request_id}/approve", json={"scopes": list(ticked)})
        assert approved.status_code == 200, approved.text
        redirect = approved.json()["redirect"]
        query = parse_qs(urlsplit(redirect).query)
        assert query["state"] == ["st"]
        return query["code"][0], verifier

    @staticmethod
    def token(c, client_id, code, verifier, redirect_uri):
        return c.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": RESOURCE,
            },
        )

    def connect(self, c, *, scope="read offline_access", ticked=("read",), client=None):
        client = client or self.register(c)
        code, verifier = self.consent(c, client["client_id"], "http://localhost:9999/cb", scope=scope, ticked=ticked)
        r = self.token(c, client["client_id"], code, verifier, "http://localhost:9999/cb")
        assert r.status_code == 200, r.text
        return client, r.json()

    @staticmethod
    def rpc(c, access_token, method="tools/list", path="/mcp"):
        return c.post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
            headers={**MCP_HEADERS, "Authorization": f"Bearer {access_token}"},
        )


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


def _tool_names(response):
    return {t["name"] for t in response.json()["result"]["tools"]}


# -- metadata and the 401 handshake ------------------------------------------------------


def test_metadata_documents(h):
    with h.client() as c:
        asm = c.get("/.well-known/oauth-authorization-server").json()
        assert asm["issuer"] == BASE
        assert "none" in asm["token_endpoint_auth_methods_supported"]
        assert asm["client_id_metadata_document_supported"] is True
        assert asm["code_challenge_methods_supported"] == ["S256"]
        assert "offline_access" in asm["scopes_supported"]
        prm = c.get("/.well-known/oauth-protected-resource/mcp").json()
        assert prm["resource"] == RESOURCE
        # The issuer a client derives from here must equal the metadata's.
        assert [s.rstrip("/") for s in prm["authorization_servers"]] == [asm["issuer"]]
        assert c.get("/.well-known/oauth-protected-resource").json() == prm


def test_unauthenticated_mcp_is_401_with_resource_metadata(h):
    with h.client() as c:
        for path in ("/mcp", "/mcp/"):
            r = c.post(path, json={}, headers=MCP_HEADERS)
            assert r.status_code == 401
            challenge = r.headers["www-authenticate"]
            assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in challenge
            assert 'scope="read offline_access"' in challenge
        bad = h.rpc(c, "not-a-token")
        assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]


# -- DCR ------------------------------------------------------------------------------------------


def test_dcr_defaults_to_public_and_honours_explicit_secret(h):
    with h.client() as c:
        public = h.register(c)
        assert public["token_endpoint_auth_method"] == "none" and "client_secret" not in public
        confidential = h.register(c, token_endpoint_auth_method="client_secret_post")
        assert confidential["client_secret"]


def test_dcr_rate_limited_per_ip(h):
    with h.client() as c:
        for _ in range(10):
            h.register(c)
        assert c.post("/register", json={"redirect_uris": ["http://localhost:1/cb"]}).status_code == 429


def test_full_flow_reads_with_its_own_scopes(h):
    with h.client() as c:
        _, tokens = h.connect(c)
        assert tokens["scope"] == "read offline_access" and tokens["refresh_token"]
        r = h.rpc(c, tokens["access_token"])
        assert r.status_code == 200
        names = _tool_names(r)
        assert "list_podcasts" in names and "add_podcast" not in names
        # Trailing slash is the same door.
        assert h.rpc(c, tokens["access_token"], path="/mcp/").status_code == 200


def test_without_offline_access_no_refresh_token(h):
    with h.client() as c:
        _, tokens = h.connect(c, scope="read")
        assert tokens.get("refresh_token") is None


def test_plain_pkce_refused(h):
    """The SDK refuses ``plain``; with a valid client and redirect the error
    goes back to the client's callback (RFC 6749 §4.1.2.1), never to consent."""
    with h.client() as c:
        client = h.register(c)
        r = h.authorize(c, client["client_id"], "http://localhost:9999/cb", "x" * 43, code_challenge_method="plain")
        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith("http://localhost:9999/cb?")
        assert parse_qs(urlsplit(location).query)["error"] == ["invalid_request"]


@pytest.mark.parametrize("uri", ["javascript:alert(document.cookie)//", "http://evil.example/cb", "data:text/html,x"])
def test_dcr_refuses_unsafe_redirect_uris(h, uri):
    with h.client() as c:
        r = c.post("/register", json={"redirect_uris": [uri], "grant_types": ["authorization_code"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri"


def test_wrong_verifier_refused(h):
    with h.client() as c:
        client = h.register(c)
        code, _ = h.consent(c, client["client_id"], "http://localhost:9999/cb")
        r = h.token(c, client["client_id"], code, "wrong-verifier-" + "x" * 40, "http://localhost:9999/cb")
        assert r.status_code == 400


def test_unregistered_redirect_never_redirects(h):
    with h.client() as c:
        client = h.register(c)
        _, challenge = h.pkce()
        r = h.authorize(c, client["client_id"], "https://evil.example/cb", challenge)
        assert r.status_code == 400 and "location" not in r.headers


# -- CIMD -----------------------------------------------------------------------------------------


def test_cimd_client_without_scope_on_random_loopback_port(h):
    with h.client() as c:
        redirect = "http://localhost:53121/callback"
        code, verifier = h.consent(c, CIMD_URL, redirect, scope="read offline_access")
        r = h.token(c, CIMD_URL, code, verifier, redirect)
        assert r.status_code == 200, r.text
        assert h.rpc(c, r.json()["access_token"]).status_code == 200
        # The consent screen warned about a loopback-only client.
        _, challenge = h.pkce()
        loc = h.authorize(c, CIMD_URL, redirect, challenge).headers["location"]
        view = c.get(f"/api/oauth/pending/{loc.split('#request=')[1]}").json()
        assert view["loopback_only"] is True and view["client_name"] == "Claude Code"


@pytest.mark.parametrize(
    "redirect",
    ["http://localhost:53121/other", "http://evil.example:53121/callback", "https://localhost:53121/callback"],
)
def test_cimd_loopback_relaxation_is_port_only(h, redirect):
    with h.client() as c:
        _, challenge = h.pkce()
        r = h.authorize(c, CIMD_URL, redirect, challenge)
        assert r.status_code == 400 and "location" not in r.headers


def test_https_redirect_port_is_exact(h):
    h.cimd_doc = {**h.cimd_doc, "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}
    with h.client() as c:
        _, challenge = h.pkce()
        assert h.authorize(c, CIMD_URL, "https://claude.ai:8443/api/mcp/auth_callback", challenge).status_code == 400
        ok = h.authorize(c, CIMD_URL, "https://claude.ai/api/mcp/auth_callback", challenge)
        assert ok.status_code == 302 and "/oauth/consent#request=" in ok.headers["location"]


# -- scopes across authorizations ---------------------------------------------------------------


def test_tools_call_without_follows_names_the_scope(h):
    with h.client() as c:
        _, tokens = h.connect(c)
        r = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "add_podcast", "arguments": {"url": "https://example.com/f.xml"}},
            },
            headers={**MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 200 and "follows" in r.text


def test_non_admin_asking_for_pipeline_gets_read_only(h):
    with h.client() as c:
        _, tokens = h.connect(c, scope="read pipeline offline_access", ticked=("read", "pipeline"))
        assert tokens["scope"] == "read offline_access"


def test_demoting_an_admin_drops_pipeline_on_the_next_call(h):
    h.repos.user.set_admin(USER_A, True)
    h.user = h.repos.user.get_by_id(USER_A)
    with h.client() as c:
        _, tokens = h.connect(c, scope="read pipeline offline_access", ticked=("read", "pipeline"))
        assert "transcribe_episodes" in _tool_names(h.rpc(c, tokens["access_token"]))
        h.repos.user.set_admin(USER_A, False)
        assert "transcribe_episodes" not in _tool_names(h.rpc(c, tokens["access_token"]))


def test_two_authorizations_with_different_scopes(h):
    with h.client() as c:
        client, t1 = h.connect(c, scope="read follows offline_access", ticked=("read",))
        assert "add_podcast" not in _tool_names(h.rpc(c, t1["access_token"]))
        _, t2 = h.connect(c, scope="read follows offline_access", ticked=("read", "follows"), client=client)
        assert h.rpc(c, t1["access_token"]).status_code == 401
        assert "add_podcast" in _tool_names(h.rpc(c, t2["access_token"]))


# -- refresh, revocation, account state -------------------------------------------------------


def test_refresh_rotates_and_replay_revokes(h):
    with h.client() as c:
        client, first = h.connect(c)

        def refresh(token):
            return c.post(
                "/token",
                data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client["client_id"]},
            )

        second = refresh(first["refresh_token"])
        assert second.status_code == 200 and second.json()["refresh_token"] != first["refresh_token"]
        new_access = second.json()["access_token"]
        assert h.rpc(c, new_access).status_code == 200
        replay = refresh(first["refresh_token"])
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        assert h.rpc(c, new_access).status_code == 401


def test_revoke_endpoint_disconnects(h):
    with h.client() as c:
        client, tokens = h.connect(c)
        r = c.post("/revoke", data={"token": tokens["refresh_token"], "client_id": client["client_id"]})
        assert r.status_code == 200
        assert h.rpc(c, tokens["access_token"]).status_code == 401


def test_connected_apps_list_and_disconnect(h):
    with h.client() as c:
        _, tokens = h.connect(c)
        [grant] = c.get("/api/me/oauth-grants").json()["grants"]
        assert grant["client_name"] == "Inspector" and grant["scopes"] == ["read"]
        assert c.delete("/api/me/oauth-grants/not-a-uuid").status_code == 404
        assert c.delete(f"/api/me/oauth-grants/{grant['id']}").status_code == 204
        assert c.delete(f"/api/me/oauth-grants/{grant['id']}").status_code == 404
        assert h.rpc(c, tokens["access_token"]).status_code == 401


def test_deny_redirects_access_denied(h):
    with h.client() as c:
        client = h.register(c)
        _, challenge = h.pkce()
        loc = h.authorize(c, client["client_id"], "http://localhost:9999/cb", challenge).headers["location"]
        request_id = loc.split("#request=")[1]
        r = c.post(f"/api/oauth/pending/{request_id}/deny")
        assert parse_qs(urlsplit(r.json()["redirect"]).query)["error"] == ["access_denied"]
        assert c.post(f"/api/oauth/pending/{request_id}/approve", json={"scopes": ["read"]}).status_code == 404


def test_suspended_account_refused(h):
    with h.client() as c:
        client, tokens = h.connect(c)
        h.repos.user.apply_status(
            USER_A, UserStatus.SUSPENDED, reason="test", changed_by=None, changed_at=datetime.now(timezone.utc)
        )
        assert h.rpc(c, tokens["access_token"]).status_code == 403
        refresh = c.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client["client_id"],
            },
        )
        assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"


def test_account_revocation_stops_the_next_call(h):
    """Ban, suspend and logout everywhere revoke every grant (spec #109)."""
    with h.client() as c:
        _, tokens = h.connect(c)
        assert h.repos.oauth.revoke_user_grants(USER_A, now=datetime.now(timezone.utc), reason="admin") == 1
        assert h.rpc(c, tokens["access_token"]).status_code == 401


def test_personal_url_still_works_beside_oauth(h):
    with h.client() as c:
        token = (
            McpTokenService(h.repos.mcp_token, ttl_days=90)
            .create_or_rotate(USER_A, requested_scopes=(), is_admin=False)
            .token
        )
        r = c.post(
            f"/mcp/{token}",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers=MCP_HEADERS,
        )
        assert r.status_code == 200
