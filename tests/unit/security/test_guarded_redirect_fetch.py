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

"""Regression tests for :func:`guarded_redirect_fetch` multi-hop handling."""

from unittest.mock import MagicMock

import pytest

from thestill.utils import url_guard
from thestill.utils.url_guard import (
    TooManyRedirects,
    UnsafeURLError,
    guarded_redirect_fetch,
)


@pytest.fixture(autouse=True)
def _allow_example_com(monkeypatch):
    """Pretend every hostname in these tests resolves to a public IP."""

    def fake_getaddrinfo(host, *_args, **_kwargs):
        return [(None, None, None, None, ("93.184.216.34", 0))]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", fake_getaddrinfo)


def _resp(status: int, location: str = "", body: bytes = b"") -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = {"Location": location} if location else {}
    response.content = body
    return response


class TestGuardedRedirectFetch:
    def test_returns_non_redirect_immediately(self):
        final = _resp(200, body=b"ok")
        get = MagicMock(return_value=final)
        result = guarded_redirect_fetch("https://example.com/x", get)
        assert result is final
        # allow_redirects is forced off by the helper so requests' own
        # redirect follower can't bypass our per-hop guard.
        assert get.call_args.kwargs["allow_redirects"] is False

    def test_single_hop_redirect_then_success(self):
        hops = [
            _resp(302, location="https://cdn.example.com/file"),
            _resp(200, body=b"ok"),
        ]
        get = MagicMock(side_effect=hops)
        result = guarded_redirect_fetch("https://example.com/x", get)
        assert result.status_code == 200
        assert [call.args[0] for call in get.call_args_list] == [
            "https://example.com/x",
            "https://cdn.example.com/file",
        ]

    def test_multi_hop_redirect_does_not_return_3xx_as_success(self):
        """The single-hop version returned the second 3xx as the final
        response. raise_for_status() does not catch 3xx, so callers wrote
        a Location-bearing HTTP body to disk. The helper must follow or
        refuse — never return a 3xx."""
        hops = [
            _resp(301, location="https://a.example.com/"),
            _resp(302, location="https://b.example.com/"),
            _resp(200, body=b"ok"),
        ]
        get = MagicMock(side_effect=hops)
        result = guarded_redirect_fetch("https://origin.example.com/", get)
        assert result.status_code == 200

    def test_relative_location_resolved_against_current_url(self):
        hops = [
            _resp(302, location="/final"),
            _resp(200, body=b"ok"),
        ]
        get = MagicMock(side_effect=hops)
        guarded_redirect_fetch("https://cdn.example.com/path/a", get)
        # The second call must see the joined URL, not the raw "/final".
        assert get.call_args_list[1].args[0] == "https://cdn.example.com/final"

    def test_blocked_redirect_target_raises(self, monkeypatch):
        # Force the second hop's hostname to resolve to loopback.
        def fake_resolve(host, *_args, **_kwargs):
            if host == "internal.evil":
                return [(None, None, None, None, ("127.0.0.1", 0))]
            return [(None, None, None, None, ("93.184.216.34", 0))]

        monkeypatch.setattr(url_guard.socket, "getaddrinfo", fake_resolve)

        hops = [_resp(302, location="http://internal.evil/admin")]
        get = MagicMock(side_effect=hops)
        with pytest.raises(UnsafeURLError):
            guarded_redirect_fetch("https://example.com/", get)

    def test_cap_exceeded_raises(self):
        redirect_chain = [_resp(302, location=f"https://h{i}.example.com/") for i in range(20)]
        get = MagicMock(side_effect=redirect_chain)
        with pytest.raises(TooManyRedirects):
            guarded_redirect_fetch("https://example.com/", get, max_redirects=3)

    def test_redirect_without_location_raises(self):
        hops = [_resp(302, location="")]
        get = MagicMock(side_effect=hops)
        with pytest.raises(UnsafeURLError):
            guarded_redirect_fetch("https://example.com/", get)
