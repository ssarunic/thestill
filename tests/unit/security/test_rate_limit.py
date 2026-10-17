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

"""Regression tests for spec #25, item 2.3 — in-process rate limiter."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from thestill.web.middleware.rate_limit import (
    _LIMITER,
    RateLimit,
    RateLimitExceeded,
    enforce_mcp_mutation_quota,
    rate_limit_dependency,
    reset_for_testing,
)


@pytest.fixture(autouse=True)
def _clean_limiter():
    reset_for_testing()
    yield
    reset_for_testing()


class TestSlidingWindow:
    def test_under_cap_passes(self):
        limit = RateLimit(max_events=3, window_seconds=10)
        for _ in range(3):
            assert _LIMITER.allow("k", limit) is True

    def test_over_cap_rejects(self):
        limit = RateLimit(max_events=2, window_seconds=10)
        assert _LIMITER.allow("k", limit) is True
        assert _LIMITER.allow("k", limit) is True
        assert _LIMITER.allow("k", limit) is False

    def test_independent_keys(self):
        limit = RateLimit(max_events=1, window_seconds=10)
        assert _LIMITER.allow("client-a", limit) is True
        # Different key has its own bucket.
        assert _LIMITER.allow("client-b", limit) is True

    def test_window_expires(self):
        limit = RateLimit(max_events=1, window_seconds=1)
        assert _LIMITER.allow("k", limit) is True
        assert _LIMITER.allow("k", limit) is False
        time.sleep(1.1)
        assert _LIMITER.allow("k", limit) is True


class TestRateLimitDependency:
    def _mk_request(self, host: str, *, trusted_proxies=None, xff: str = ""):
        req = MagicMock()
        req.client = MagicMock(host=host)
        req.headers = {"X-Forwarded-For": xff} if xff else {}
        # Wire up request.app.state.app_state.config so _resolve_client_ip
        # can locate the trusted-proxy allowlist without a real FastAPI app.
        req.app.state.app_state.config = SimpleNamespace(
            trusted_proxies=trusted_proxies or [],
        )
        return req

    def test_blocks_after_cap(self):
        limit = RateLimit(max_events=2, window_seconds=10)
        dep = rate_limit_dependency(limit, "x")
        req = self._mk_request("203.0.113.5")
        dep(req)
        dep(req)
        with pytest.raises(Exception) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers

    def test_untrusted_peer_ignores_xff(self):
        """A spoofed X-Forwarded-For from an untrusted peer must NOT be used
        as the rate-limit key — otherwise clients can evade the bucket by
        rotating forwarded IPs."""
        limit = RateLimit(max_events=1, window_seconds=10)
        dep = rate_limit_dependency(limit, "x")
        req = self._mk_request("203.0.113.5", xff="198.51.100.42")
        dep(req)
        with pytest.raises(Exception) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 429

    def test_trusted_proxy_uses_xff_client(self):
        """When the peer is a trusted proxy, XFF identifies the real client —
        two different XFF clients behind the same proxy share no bucket.

        Post-review fix (spec #25 item 2.3): previously every user behind a
        reverse proxy shared one bucket because only request.client.host was
        consulted. A single abusive client could lock everyone out."""
        limit = RateLimit(max_events=1, window_seconds=10)
        dep = rate_limit_dependency(limit, "x")
        # Client A behind the trusted proxy.
        req_a = self._mk_request("10.0.0.1", trusted_proxies=["10.0.0.1"], xff="203.0.113.5")
        # Client B behind the same proxy.
        req_b = self._mk_request("10.0.0.1", trusted_proxies=["10.0.0.1"], xff="203.0.113.9")
        dep(req_a)
        # B must not be blocked because A used the bucket.
        dep(req_b)
        # A's second request exhausts A's own bucket.
        with pytest.raises(Exception) as exc_info:
            dep(req_a)
        assert exc_info.value.status_code == 429

    def test_trusted_proxy_strips_proxy_hops_from_xff(self):
        """Multiple-proxy chain: walk right-to-left, skip known hops."""
        limit = RateLimit(max_events=1, window_seconds=10)
        dep = rate_limit_dependency(limit, "x")
        # Real client 203.0.113.5 → edge-proxy 172.16.0.1 → our-proxy 10.0.0.1 → us.
        req = self._mk_request(
            "10.0.0.1",
            trusted_proxies=["10.0.0.1", "172.16.0.1"],
            xff="203.0.113.5, 172.16.0.1",
        )
        dep(req)
        with pytest.raises(Exception):
            dep(req)


class TestMcpMutationQuota:
    def test_allows_under_cap(self, monkeypatch):
        monkeypatch.setattr(
            "thestill.web.middleware.rate_limit.MCP_MUTATION_LIMIT",
            RateLimit(max_events=2, window_seconds=10),
        )
        enforce_mcp_mutation_quota("add_podcast")
        enforce_mcp_mutation_quota("add_podcast")

    def test_rejects_over_cap(self, monkeypatch):
        monkeypatch.setattr(
            "thestill.web.middleware.rate_limit.MCP_MUTATION_LIMIT",
            RateLimit(max_events=1, window_seconds=10),
        )
        enforce_mcp_mutation_quota("add_podcast")
        with pytest.raises(RateLimitExceeded):
            enforce_mcp_mutation_quota("add_podcast")

    def test_independent_per_tool(self, monkeypatch):
        """A quota on add_podcast should not consume the quota for remove_podcast."""
        monkeypatch.setattr(
            "thestill.web.middleware.rate_limit.MCP_MUTATION_LIMIT",
            RateLimit(max_events=1, window_seconds=10),
        )
        enforce_mcp_mutation_quota("add_podcast")
        enforce_mcp_mutation_quota("remove_podcast")  # different bucket

    def test_independent_per_session(self, monkeypatch):
        """Post-review fix (spec #25 item 2.3): a hot client must not burn
        the add_podcast quota for an unrelated client. Two different
        session_keys mean two different buckets."""
        monkeypatch.setattr(
            "thestill.web.middleware.rate_limit.MCP_MUTATION_LIMIT",
            RateLimit(max_events=1, window_seconds=10),
        )
        enforce_mcp_mutation_quota("add_podcast", session_key="session-a")
        # Same tool, different session — must NOT be throttled.
        enforce_mcp_mutation_quota("add_podcast", session_key="session-b")
        # Session A's second call IS throttled.
        with pytest.raises(RateLimitExceeded):
            enforce_mcp_mutation_quota("add_podcast", session_key="session-a")


class TestTrustedProxyRanges:
    """TRUSTED_PROXIES accepts CIDR ranges: behind Docker the proxy's
    container address changes, so production trusts the bridge range."""

    def _request(self, peer: str, xff: str, trusted):
        req = MagicMock()
        req.client = MagicMock(host=peer)
        req.headers = {"X-Forwarded-For": xff}
        req.app.state.app_state.config = SimpleNamespace(trusted_proxies=trusted)
        return req

    def test_a_peer_inside_a_trusted_range_forwards_the_client(self):
        from thestill.web.middleware.rate_limit import resolve_client_ip

        assert resolve_client_ip(self._request("172.18.0.4", "203.0.113.5", ["172.16.0.0/12"])) == "203.0.113.5"
        # Two readers behind the same proxy are two clients, not one.
        assert resolve_client_ip(self._request("172.18.0.4", "198.51.100.9", ["172.16.0.0/12"])) == "198.51.100.9"

    def test_a_peer_outside_the_range_is_not_trusted(self):
        from thestill.web.middleware.rate_limit import resolve_client_ip

        assert resolve_client_ip(self._request("203.0.113.7", "198.51.100.9", ["172.16.0.0/12"])) == "203.0.113.7"

    def test_trusted_hops_inside_the_range_are_skipped(self):
        from thestill.web.middleware.rate_limit import resolve_client_ip

        req = self._request("172.18.0.4", "203.0.113.5, 172.18.0.9", ["172.16.0.0/12"])
        assert resolve_client_ip(req) == "203.0.113.5"

    def test_membership(self):
        from thestill.web.middleware.rate_limit import trusted_proxy_set

        trusted = trusted_proxy_set(SimpleNamespace(trusted_proxies=["10.0.0.1", "172.16.0.0/12", "not-an-ip"]))
        assert "10.0.0.1" in trusted and "172.31.255.1" in trusted and "not-an-ip" in trusted
        assert "10.0.0.2" not in trusted and "192.168.1.1" not in trusted and "garbage" not in trusted
        assert not trusted_proxy_set(SimpleNamespace(trusted_proxies=[]))
