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

"""Spec #71 — GET /api/status/mcp (remote MCP connector info)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.web.dependencies import get_app_state
from thestill.web.routes import api_status

SECRET = "s" * 48


def _client(config: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(api_status.router, prefix="/api/status")
    state = MagicMock()
    state.config = config
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app)


@pytest.fixture
def enabled_config():
    config = MagicMock()
    config.mcp_http_enabled = True
    config.mcp_http_secret = SECRET
    config.public_base_url = "https://pods.example.com"
    return config


def test_disabled_reports_enabled_false():
    config = MagicMock()
    config.mcp_http_enabled = False
    config.mcp_http_secret = ""
    response = _client(config).get("/api/status/mcp")
    assert response.status_code == 200
    assert response.json()["mcp"] == {"enabled": False}


def test_enabled_returns_connector_url(enabled_config):
    response = _client(enabled_config).get("/api/status/mcp")
    assert response.status_code == 200
    mcp = response.json()["mcp"]
    assert mcp["enabled"] is True
    assert mcp["url"] == f"https://pods.example.com/mcp/{SECRET}"
    assert mcp["transport"] == "streamable-http"


def test_enabled_without_public_base_url_uses_request_base(enabled_config):
    enabled_config.public_base_url = ""
    response = _client(enabled_config).get("/api/status/mcp")
    mcp = response.json()["mcp"]
    # TestClient's base URL — proves the fallback derives from the request.
    assert mcp["url"] == f"http://testserver/mcp/{SECRET}"
