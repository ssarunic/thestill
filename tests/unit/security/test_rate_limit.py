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
from unittest.mock import MagicMock

import pytest

from thestill.web.middleware.rate_limit import (
    RateLimit,
    RateLimitExceeded,
    _LIMITER,
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
    def _mk_request(self, host: str):
        req = MagicMock()
        req.client = MagicMock(host=host)
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

    def test_does_not_consult_x_forwarded_for(self):
        """Attacker spoofing X-Forwarded-For must not bypass the limiter."""
        limit = RateLimit(max_events=1, window_seconds=10)
        dep = rate_limit_dependency(limit, "x")
        req = self._mk_request("203.0.113.5")
        # Even with a different X-Forwarded-For, the per-IP bucket stays.
        req.headers = {"X-Forwarded-For": "198.51.100.42"}
        dep(req)
        with pytest.raises(Exception) as exc_info:
            dep(req)
        assert exc_info.value.status_code == 429


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
