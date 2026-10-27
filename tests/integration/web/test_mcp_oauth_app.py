"""Spec #109 — MCP OAuth wiring in the real app (``create_app``).

The flow itself is covered in ``tests/unit/web/test_mcp_oauth_flow.py``;
this checks the flag: on, the OAuth routes and the exact-path ``/mcp``
guard are registered ahead of the mount and the SPA; off, none of it is.
"""

from fastapi.testclient import TestClient
from starlette.routing import Route

from thestill.web.app import create_app


def _client(app_config, app_state, **overrides):
    config = app_config.model_copy(
        update={"mcp_http_enabled": True, "public_base_url": "http://localhost", **overrides}
    )
    app = create_app(config)
    app.state.app_state = app_state
    return TestClient(app, base_url="http://localhost")


def test_flag_on_registers_oauth_and_bearer_guard(app_config, app_state):
    client = _client(app_config, app_state, mcp_oauth_enabled=True)
    asm = client.get("/.well-known/oauth-authorization-server")
    assert asm.status_code == 200 and asm.json()["issuer"] == "http://localhost"
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == "http://localhost/mcp"
    r = client.post("/mcp", json={})
    assert r.status_code == 401 and "resource_metadata=" in r.headers["www-authenticate"]


def test_flag_off_registers_nothing(app_config, app_state):
    client = _client(app_config, app_state, mcp_oauth_enabled=False)
    # Routes only: the personal-URL Mount at /mcp is there either way.
    paths = {route.path for route in client.app.routes if isinstance(route, Route)}
    assert not paths & {
        "/authorize",
        "/token",
        "/register",
        "/revoke",
        "/mcp",
        "/.well-known/oauth-authorization-server",
    }
    assert client.post("/mcp", json={}, follow_redirects=False).status_code != 401


def test_consent_route_cannot_be_framed(app_config, app_state):
    """The app-wide security headers cover the consent page (spec #109)."""
    client = _client(app_config, app_state, mcp_oauth_enabled=True)
    headers = client.get("/oauth/consent").headers
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
