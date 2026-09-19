"""Spec #78 — GET /api/status/mcp reports only the feature flag."""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from thestill.web.dependencies import get_app_state
from thestill.web.routes import api_status


def _client(enabled: bool) -> TestClient:
    app = FastAPI()
    app.include_router(api_status.router, prefix="/api/status")
    state = MagicMock()
    state.config.mcp_http_enabled = enabled
    app.dependency_overrides[get_app_state] = lambda: state
    return TestClient(app)


def test_disabled_reports_enabled_false():
    assert _client(False).get("/api/status/mcp").json()["mcp"] == {"enabled": False}


def test_enabled_reports_only_the_flag_never_a_url():
    body = _client(True).get("/api/status/mcp").json()["mcp"]
    assert body == {"enabled": True}
