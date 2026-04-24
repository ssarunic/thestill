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

"""Regression tests for spec #25 item 3.1 — security response headers."""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from thestill.web.middleware.security_headers import SecurityHeadersMiddleware


def _make_app(*, is_production: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, is_production=is_production)

    @app.get("/x")
    def hello():
        return {"ok": True}

    return app


class TestSecurityHeaders:
    def test_core_headers_always_present(self):
        client = TestClient(_make_app(is_production=False))
        resp = client.get("/x")
        assert resp.status_code == 200
        assert "Content-Security-Policy" in resp.headers
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_hsts_only_in_production(self):
        dev = TestClient(_make_app(is_production=False)).get("/x")
        prod = TestClient(_make_app(is_production=True)).get("/x")
        assert "Strict-Transport-Security" not in dev.headers
        assert "Strict-Transport-Security" in prod.headers
        assert "max-age=31536000" in prod.headers["Strict-Transport-Security"]

    def test_csp_denies_inline_scripts(self):
        """script-src must NOT allow 'unsafe-inline' — that is the whole point."""
        client = TestClient(_make_app(is_production=True))
        csp = client.get("/x").headers["Content-Security-Policy"]
        # Extract the script-src directive.
        for chunk in csp.split(";"):
            chunk = chunk.strip()
            if chunk.startswith("script-src"):
                assert "'unsafe-inline'" not in chunk
                assert "'self'" in chunk
                break
        else:  # no script-src clause
            pytest.fail(f"CSP has no script-src directive: {csp!r}")

    def test_csp_denies_framing(self):
        csp = TestClient(_make_app(is_production=True)).get("/x").headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in csp

    def test_route_set_header_not_overwritten(self):
        """A deliberate per-route header override must win."""
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware, is_production=True)

        @app.get("/strict")
        def strict():
            from fastapi.responses import Response

            r = Response("ok")
            r.headers["X-Frame-Options"] = "SAMEORIGIN"
            return r

        resp = TestClient(app).get("/strict")
        assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
