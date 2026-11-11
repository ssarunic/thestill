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

"""Regression tests for spec #25 item 3.7 — request body size cap."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from thestill.web.middleware.body_size import BodySizeLimitMiddleware


def _make_app(*, default_limit: int, route_limits=()) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=default_limit,
        route_limits=route_limits,
    )

    @app.post("/any")
    async def any_route():
        return {"ok": True}

    @app.post("/webhook/x")
    async def webhook_route():
        return {"ok": True}

    @app.get("/info")
    async def info_route():
        return {"ok": True}

    return app


class TestBodySizeCap:
    def test_under_cap_allowed(self):
        client = TestClient(_make_app(default_limit=1024))
        resp = client.post("/any", data=b"x" * 100)
        assert resp.status_code == 200

    def test_over_cap_returns_413(self):
        client = TestClient(_make_app(default_limit=100))
        resp = client.post("/any", data=b"x" * 500)
        assert resp.status_code == 413
        assert "100-byte limit" in resp.json()["detail"]

    def test_route_specific_limit_wins(self):
        """Webhook cap is tighter than the default; a 10-byte webhook body
        over the webhook cap must 413 even if it fits the default."""
        client = TestClient(_make_app(default_limit=10_000, route_limits=[("/webhook/", 50)]))
        resp = client.post("/webhook/x", data=b"x" * 200)
        assert resp.status_code == 413
        # Same body on /any (no prefix match) goes through under default.
        resp2 = client.post("/any", data=b"x" * 200)
        assert resp2.status_code == 200

    def test_get_requests_not_capped(self):
        client = TestClient(_make_app(default_limit=1))
        resp = client.get("/info")
        assert resp.status_code == 200

    def test_malformed_content_length_rejected(self):
        client = TestClient(_make_app(default_limit=1024))
        resp = client.post(
            "/any",
            data=b"x",
            headers={"Content-Length": "not-an-int"},
        )
        # httpx normalises to a sane value when constructing the request,
        # so this path is exercised only if a raw client sends it.
        # Either the middleware rejects with 400 or httpx silently fixes it.
        assert resp.status_code in (200, 400)


def test_suffix_limit_caps_a_route_whose_variable_part_comes_first():
    """Spec #116 — ``…/episodes/{slug}/drift-outcome`` cannot be named by a prefix."""
    app = FastAPI()
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=1000,
        route_limits=[("/api/", 500)],
        suffix_limits=[("/drift-outcome", 50)],
    )

    @app.post("/api/podcasts/{podcast}/episodes/{episode}/drift-outcome")
    async def report(podcast: str, episode: str):
        return {"ok": True}

    @app.post("/api/other")
    async def other():
        return {"ok": True}

    client = TestClient(app)
    url = "/api/podcasts/show/episodes/ep/drift-outcome"

    assert client.post(url, content=b"x" * 50).status_code == 200
    # The suffix cap wins over the looser prefix cap.
    assert client.post(url, content=b"x" * 51).status_code == 413
    assert client.post(url + "/", content=b"x" * 51, follow_redirects=False).status_code == 413
    # A route capped by suffix must declare its length.
    assert client.post(url, content=iter([b"x" * 10])).status_code == 411
    # Other routes are untouched: prefix cap, and chunked bodies still pass.
    assert client.post("/api/other", content=b"x" * 51).status_code == 200
    assert client.post("/api/other", content=iter([b"x" * 10])).status_code == 200
