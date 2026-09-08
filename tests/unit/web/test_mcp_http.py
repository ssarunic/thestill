"""Spec #78 — remote MCP over Streamable HTTP behind per-user tokens.

Covers the token guard (unknown/revoked/expired tokens and malformed paths
are indistinguishable empty 404s), a full JSON-RPC round trip over the
mounted transport, the per-token HTTP rate limit, the last-use bump, the
identity stashed for the handlers, and the access-log redaction.
"""

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.repositories.factory import make_repositories
from thestill.services.mcp_token_service import McpTokenService
from thestill.utils.config import Config, load_config
from thestill.utils.datetime_utils import now_utc
from thestill.web.mcp_http import MOUNT_PATH, build_mcp_http, mount_base_url
from thestill.web.middleware import rate_limit

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Point the internal ``load_config()`` calls in setup_tools/resources
    at the tmp storage dir instead of the developer's real .env."""
    empty_env = tmp_path / ".env"
    empty_env.touch()
    monkeypatch.setenv("THESTILL_ENV_FILE", str(empty_env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MCP_HTTP_ENABLED", raising=False)
    rate_limit.reset_for_testing()
    yield monkeypatch
    rate_limit.reset_for_testing()


def _config(tmp_path, **overrides) -> Config:
    base = dict(
        storage_path=tmp_path,
        multi_user=True,
        jwt_secret_key="x" * 64,
        cookie_secure=False,
        mcp_http_enabled=True,
    )
    base.update(overrides)
    return Config(**base)


class Harness:
    """Config + repos + a TestClient with the runtime mounted at /mcp."""

    def __init__(self, tmp_path, **config_overrides):
        self.config = _config(tmp_path, **config_overrides)
        self.repos = make_repositories(self.config)
        self.tokens = McpTokenService(self.repos.mcp_token, ttl_days=self.config.mcp_token_ttl_days)
        self.repos.user.save(User(id=USER_A, email="a@example.com", is_admin=False))
        self.repos.user.save(User(id=USER_B, email="b@example.com", is_admin=True))
        runtime = build_mcp_http(self.config, self.repos)
        assert runtime is not None

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with runtime.lifespan():
                yield

        app = FastAPI(lifespan=lifespan)
        app.mount(MOUNT_PATH, runtime)
        self._app = app

    def mint(self, user_id=USER_A, scopes=(), is_admin=False):
        return self.tokens.create_or_rotate(user_id, requested_scopes=scopes, is_admin=is_admin).token

    def client(self) -> TestClient:
        return TestClient(self._app)

    @staticmethod
    def rpc(client, token, method, params=None, id_=1):
        return client.post(
            f"/mcp/{token}",
            json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}},
            headers=MCP_HEADERS,
        )


@pytest.fixture
def harness(isolated_env, tmp_path):
    return Harness(tmp_path)


class TestTokenGuard:
    def test_unknown_token_is_404(self, harness):
        with harness.client() as c:
            assert c.post("/mcp/not-a-token", json={}).status_code == 404

    def test_bare_mount_is_404(self, harness):
        with harness.client() as c:
            assert c.get("/mcp/").status_code == 404

    def test_extra_path_segment_is_404(self, harness):
        token = harness.mint()
        with harness.client() as c:
            assert c.post(f"/mcp/{token}/extra", json={}).status_code == 404

    def test_guard_404_body_is_empty(self, harness):
        """Indistinguishable from "no such route" — no JSON-RPC envelope."""
        with harness.client() as c:
            assert c.post("/mcp/not-a-token", json={}).content == b""

    def test_revoked_token_is_404_on_next_request(self, harness):
        token = harness.mint()
        with harness.client() as c:
            assert Harness.rpc(c, token, "tools/list").status_code == 200
            harness.tokens.revoke(USER_A)
            assert Harness.rpc(c, token, "tools/list").status_code == 404

    def test_rotated_token_kills_the_old_url(self, harness):
        old = harness.mint()
        new = harness.mint()
        with harness.client() as c:
            assert Harness.rpc(c, old, "tools/list").status_code == 404
            assert Harness.rpc(c, new, "tools/list").status_code == 200

    def test_expired_token_is_404(self, isolated_env, tmp_path):
        h = Harness(tmp_path, mcp_token_ttl_days=1)
        token = h.mint()
        # Backdate expiry directly in the row.
        row = h.repos.mcp_token.get(USER_A)
        h.repos.mcp_token.upsert(row.model_copy(update={"expires_at": now_utc() - timedelta(seconds=1)}))
        with h.client() as c:
            assert Harness.rpc(c, token, "tools/list").status_code == 404

    def test_deleted_user_is_404(self, harness):
        token = harness.mint()
        harness.repos.user.delete(USER_A)
        with harness.client() as c:
            assert Harness.rpc(c, token, "tools/list").status_code == 404


class TestStreamableHttpRoundTrip:
    def test_initialize_and_tools_list(self, harness):
        token = harness.mint()
        with harness.client() as c:
            init = Harness.rpc(
                c,
                token,
                "initialize",
                {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
            )
            assert init.status_code == 200
            assert init.json()["result"]["serverInfo"]["name"] == "thestill-mcp"
            # Stateless transport: a second independent request works with no session header.
            listed = Harness.rpc(c, token, "tools/list", id_=2)
            assert listed.status_code == 200
            names = {t["name"] for t in listed.json()["result"]["tools"]}
            assert {"list_podcasts", "search_corpus", "get_transcript"} <= names


class TestPerTokenRateLimit:
    def test_over_limit_is_429_with_retry_after_and_other_token_unaffected(self, isolated_env, tmp_path):
        h = Harness(tmp_path, mcp_token_requests_per_minute=3)
        a = h.mint(USER_A)
        b = h.mint(USER_B)
        with h.client() as c:
            for _ in range(3):
                assert Harness.rpc(c, a, "tools/list").status_code == 200
            over = Harness.rpc(c, a, "tools/list")
            assert over.status_code == 429
            assert over.headers["Retry-After"] == "60"
            assert over.content == b""
            assert Harness.rpc(c, b, "tools/list").status_code == 200


class TestLastUse:
    def test_last_used_is_bumped(self, harness):
        token = harness.mint()
        assert harness.repos.mcp_token.get(USER_A).last_used_at is None
        with harness.client() as c:
            Harness.rpc(c, token, "tools/list")
        row = harness.repos.mcp_token.get(USER_A)
        assert row.last_used_at is not None
        assert row.last_used_ip == "testclient"


class TestIdentityOnScope:
    def test_guard_stashes_user_admin_and_scopes(self, harness, monkeypatch):
        """The handlers read identity off the ASGI scope; prove the guard
        puts it there with the *current* admin flag."""
        from thestill.mcp import identity as identity_mod

        captured = {}
        original = harness._app  # noqa: SLF001
        runtime = next(r.app for r in original.routes if getattr(r, "path", None) == MOUNT_PATH)
        real_handle = runtime.session_manager.handle_request

        async def spy(scope, receive, send):
            captured.update(scope["state"])
            await real_handle(scope, receive, send)

        monkeypatch.setattr(runtime.session_manager, "handle_request", spy)
        token = harness.mint(USER_B, scopes=("follows", "pipeline"), is_admin=True)
        with harness.client() as c:
            Harness.rpc(c, token, "tools/list")
            assert captured[identity_mod.STATE_USER].id == USER_B
            assert captured[identity_mod.STATE_IS_ADMIN] is True
            assert captured[identity_mod.STATE_SCOPES] == {"read", "follows", "pipeline"}

            # Demote and call again: the admin flag flips with no rotate.
            # (UserRepository.save() deliberately never touches is_admin.)
            import sqlite3

            with sqlite3.connect(str(harness.config.database_path)) as conn:
                conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (USER_B,))
            Harness.rpc(c, token, "tools/list", id_=2)
            assert captured[identity_mod.STATE_IS_ADMIN] is False


class TestBuildMcpHttp:
    def test_disabled_returns_none(self, isolated_env, tmp_path):
        config = _config(tmp_path, mcp_http_enabled=False)
        assert build_mcp_http(config, make_repositories(config)) is None


class TestConfig:
    def test_defaults(self, isolated_env):
        config = load_config()
        assert config.mcp_http_enabled is False
        assert config.mcp_token_ttl_days == 90
        assert config.mcp_token_requests_per_minute == 120

    def test_env_overrides(self, isolated_env):
        isolated_env.setenv("MCP_HTTP_ENABLED", "true")
        isolated_env.setenv("MCP_TOKEN_TTL_DAYS", "0")
        isolated_env.setenv("MCP_TOKEN_REQUESTS_PER_MINUTE", "7")
        config = load_config()
        assert config.mcp_http_enabled is True
        assert config.mcp_token_ttl_days == 0
        assert config.mcp_token_requests_per_minute == 7


class TestMountBaseUrl:
    def test_prefers_public_base_url(self, tmp_path):
        config = _config(tmp_path, public_base_url="https://pods.example.com")
        assert mount_base_url(config, "http://localhost:8000/") == "https://pods.example.com/mcp"

    def test_falls_back_to_request_base(self, tmp_path):
        assert mount_base_url(_config(tmp_path), "http://localhost:8000/") == "http://localhost:8000/mcp"


class TestLogRedaction:
    def test_mcp_paths_are_redacted(self):
        from thestill.web.middleware.logging_middleware import _safe_endpoint

        assert _safe_endpoint("/mcp/" + "t" * 64) == "/mcp/<redacted>"
        assert _safe_endpoint("/api/podcasts") == "/api/podcasts"

    def test_uvicorn_access_filter_rewrites_path_arg(self):
        """uvicorn's access logger bypasses structlog; the filter must
        rewrite the path arg so the token never reaches the formatted
        line, while leaving other requests and record shapes untouched."""
        import logging

        from thestill.utils.log_safety import UvicornAccessRedactFilter

        token = "t" * 64
        flt = UvicornAccessRedactFilter()
        fmt = '%s - "%s %s HTTP/%s" %d'
        rec = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0, fmt, ("127.0.0.1:1", "POST", f"/mcp/{token}", "1.1", 200), None
        )
        assert flt.filter(rec) is True
        line = rec.getMessage()
        assert token not in line
        assert '"POST /mcp/<redacted> HTTP/1.1" 200' in line

        plain = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            0,
            fmt,
            ("127.0.0.1:1", "GET", "/api/podcasts?x=1", "1.1", 200),
            None,
        )
        flt.filter(plain)
        assert "/api/podcasts?x=1" in plain.getMessage()

        odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "no args", None, None)
        assert flt.filter(odd) is True

    def test_install_is_idempotent(self):
        import logging

        from thestill.utils.log_safety import UvicornAccessRedactFilter, install_uvicorn_access_redaction

        access_logger = logging.getLogger("uvicorn.access")
        for f in list(access_logger.filters):
            if isinstance(f, UvicornAccessRedactFilter):
                access_logger.removeFilter(f)
        try:
            install_uvicorn_access_redaction()
            install_uvicorn_access_redaction()
            assert len([f for f in access_logger.filters if isinstance(f, UvicornAccessRedactFilter)]) == 1
        finally:
            for f in list(access_logger.filters):
                if isinstance(f, UvicornAccessRedactFilter):
                    access_logger.removeFilter(f)
