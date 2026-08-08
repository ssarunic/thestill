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

"""Spec #71 Phase 1 — remote MCP over Streamable HTTP.

Covers the capability-URL guard (wrong/missing/extra-segment secrets are
indistinguishable 404s), a full JSON-RPC round trip over the mounted
transport, the fail-fast config validation, connector-URL construction,
and the access-log secret redaction.
"""

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.utils.config import Config, load_config
from thestill.web.mcp_http import MOUNT_PATH, McpHttpRuntime, build_mcp_http, connector_url

SECRET = "s" * 48

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


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
    monkeypatch.delenv("MCP_HTTP_SECRET", raising=False)
    return monkeypatch


@pytest.fixture
def mcp_client(isolated_env, tmp_path):
    """TestClient with the runtime mounted at /mcp the way app.py mounts it,
    and the session manager's task group running via the lifespan."""
    config = Config(
        storage_path=tmp_path,
        multi_user=False,
        jwt_secret_key="x" * 64,
        cookie_secure=False,
        mcp_http_enabled=True,
        mcp_http_secret=SECRET,
    )
    runtime = build_mcp_http(config)
    assert runtime is not None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with runtime.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount(MOUNT_PATH, runtime)

    with TestClient(app) as client:
        yield client


class TestSecretGuard:
    def test_wrong_secret_is_404(self, mcp_client):
        assert mcp_client.post("/mcp/wrong-secret", json={}).status_code == 404

    def test_bare_mount_is_404(self, mcp_client):
        assert mcp_client.get("/mcp/").status_code == 404

    def test_extra_path_segment_is_404(self, mcp_client):
        assert mcp_client.post(f"/mcp/{SECRET}/extra", json={}).status_code == 404

    def test_guard_404_body_is_empty(self, mcp_client):
        """The 404 must be indistinguishable from "no such route" — no
        JSON-RPC error envelope that would confirm the endpoint exists."""
        response = mcp_client.post("/mcp/wrong-secret", json={})
        assert response.content == b""


class TestStreamableHttpRoundTrip:
    def test_initialize_and_tools_list(self, mcp_client):
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        response = mcp_client.post(f"/mcp/{SECRET}", json=init, headers=MCP_HEADERS)
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "thestill-mcp"

        # Stateless transport: a second independent request works with no
        # session header (each request gets a fresh transport).
        response = mcp_client.post(
            f"/mcp/{SECRET}",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=MCP_HEADERS,
        )
        assert response.status_code == 200
        tool_names = {tool["name"] for tool in response.json()["result"]["tools"]}
        # Spot-check the surface matches the stdio server's tool set.
        assert {"list_podcasts", "search_corpus", "get_transcript"} <= tool_names


class TestBuildMcpHttp:
    def test_disabled_returns_none(self, tmp_path):
        config = Config(
            storage_path=tmp_path,
            jwt_secret_key="x" * 64,
            cookie_secure=False,
        )
        assert build_mcp_http(config) is None

    def test_short_secret_rejected(self, tmp_path):
        config = Config(
            storage_path=tmp_path,
            jwt_secret_key="x" * 64,
            cookie_secure=False,
            mcp_http_enabled=True,
            mcp_http_secret="too-short",
        )
        with pytest.raises(ValueError, match="at least 32 characters"):
            McpHttpRuntime(config)


class TestConfigValidation:
    def test_enabled_without_secret_fails_at_boot(self, isolated_env):
        isolated_env.setenv("MCP_HTTP_ENABLED", "true")
        with pytest.raises(ValueError, match="MCP_HTTP_SECRET"):
            load_config()

    def test_enabled_with_short_secret_fails_at_boot(self, isolated_env):
        isolated_env.setenv("MCP_HTTP_ENABLED", "true")
        isolated_env.setenv("MCP_HTTP_SECRET", "short")
        with pytest.raises(ValueError, match="at least"):
            load_config()

    def test_enabled_with_valid_secret_loads(self, isolated_env):
        isolated_env.setenv("MCP_HTTP_ENABLED", "true")
        isolated_env.setenv("MCP_HTTP_SECRET", SECRET)
        config = load_config()
        assert config.mcp_http_enabled is True
        assert config.mcp_http_secret == SECRET

    def test_disabled_needs_no_secret(self, isolated_env):
        config = load_config()
        assert config.mcp_http_enabled is False


class TestConnectorUrl:
    def test_prefers_public_base_url(self, tmp_path):
        config = Config(
            storage_path=tmp_path,
            jwt_secret_key="x" * 64,
            cookie_secure=False,
            mcp_http_secret=SECRET,
            public_base_url="https://pods.example.com",
        )
        assert connector_url(config, "http://localhost:8000/") == f"https://pods.example.com/mcp/{SECRET}"

    def test_falls_back_to_request_base(self, tmp_path):
        config = Config(
            storage_path=tmp_path,
            jwt_secret_key="x" * 64,
            cookie_secure=False,
            mcp_http_secret=SECRET,
        )
        assert connector_url(config, "http://localhost:8000/") == f"http://localhost:8000/mcp/{SECRET}"


class TestLogRedaction:
    def test_mcp_paths_are_redacted(self):
        from thestill.web.middleware.logging_middleware import _safe_endpoint

        assert _safe_endpoint(f"/mcp/{SECRET}") == "/mcp/<redacted>"
        assert _safe_endpoint("/api/podcasts") == "/api/podcasts"
