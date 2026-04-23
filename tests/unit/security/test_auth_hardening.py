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

"""Regression tests for spec #25 items 2.1 (cookie) and 2.4 (OAuth redirect)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from thestill.web.routes import auth as auth_route


def _mk_state(
    *,
    cookie_secure: bool = True,
    trusted_proxies=None,
    public_base_url: str = "",
):
    config = SimpleNamespace(
        cookie_secure=cookie_secure,
        trusted_proxies=trusted_proxies or [],
        public_base_url=public_base_url,
    )
    return SimpleNamespace(config=config)


def _mk_request(client_host: str, headers: dict, url_scheme: str = "http", url_netloc: str = "localhost:8000"):
    req = MagicMock()
    req.client = SimpleNamespace(host=client_host)
    req.headers = headers
    req.url = SimpleNamespace(scheme=url_scheme, netloc=url_netloc)
    return req


class TestSetAuthCookie:
    def test_secure_true_by_default(self):
        response = MagicMock()
        state = _mk_state(cookie_secure=True)
        auth_route._set_auth_cookie(response, "tok", state)
        kwargs = response.set_cookie.call_args.kwargs
        assert kwargs["secure"] is True
        assert kwargs["samesite"] == "strict"
        assert kwargs["httponly"] is True

    def test_can_be_disabled_for_local_dev(self):
        response = MagicMock()
        state = _mk_state(cookie_secure=False)
        auth_route._set_auth_cookie(response, "tok", state)
        kwargs = response.set_cookie.call_args.kwargs
        assert kwargs["secure"] is False
        # samesite stays strict even when secure is relaxed.
        assert kwargs["samesite"] == "strict"


class TestRedirectUri:
    def test_untrusted_host_header_ignored(self):
        """Attacker-supplied Host header must never land in the OAuth redirect."""
        state = _mk_state(
            trusted_proxies=["10.0.0.1"],
            public_base_url="https://thestill.example.com",
        )
        req = _mk_request(
            client_host="203.0.113.42",  # NOT in trusted_proxies
            headers={"X-Forwarded-Host": "evil.com", "X-Forwarded-Proto": "https"},
            url_scheme="https",
            url_netloc="thestill.example.com",
        )
        uri = auth_route._get_redirect_uri(req, state)
        assert uri == "https://thestill.example.com/api/auth/google/callback"
        assert "evil.com" not in uri

    def test_trusted_proxy_headers_honoured(self):
        state = _mk_state(trusted_proxies=["10.0.0.1"])
        req = _mk_request(
            client_host="10.0.0.1",
            headers={"X-Forwarded-Host": "public.example.com", "X-Forwarded-Proto": "https"},
            url_scheme="http",
            url_netloc="10.0.0.5:8000",
        )
        uri = auth_route._get_redirect_uri(req, state)
        assert uri == "https://public.example.com/api/auth/google/callback"

    def test_fails_closed_without_public_base_url(self):
        """Post-review hardening: no trusted proxy AND no public_base_url
        must refuse the request rather than fall back to the (spoofable)
        ASGI Host. The previous version of this test asserted the
        vulnerable behaviour and was wrong."""
        state = _mk_state(trusted_proxies=[], public_base_url="")
        req = _mk_request(
            client_host="127.0.0.1",
            headers={"X-Forwarded-Host": "evil.com"},
            url_scheme="http",
            url_netloc="localhost:8000",
        )
        with pytest.raises(HTTPException) as exc_info:
            auth_route._get_redirect_uri(req, state)
        assert exc_info.value.status_code == 500
        assert "PUBLIC_BASE_URL" in exc_info.value.detail
