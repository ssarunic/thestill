"""Spec #78 Phase 2 — per-token HTTP request limit primitive."""

import pytest

from thestill.web.middleware import rate_limit


@pytest.fixture(autouse=True)
def _reset():
    rate_limit.reset_for_testing()
    yield
    rate_limit.reset_for_testing()


def test_miss_budget_peeks_without_recording():
    ip = "203.0.113.9"
    limit = rate_limit.RateLimit(max_events=2, window_seconds=60)
    assert rate_limit._LIMITER.exhausted(f"mcp-miss:{ip}", limit) is False  # noqa: SLF001
    rate_limit._LIMITER.allow(f"mcp-miss:{ip}", limit)  # noqa: SLF001
    rate_limit._LIMITER.allow(f"mcp-miss:{ip}", limit)  # noqa: SLF001
    assert rate_limit._LIMITER.exhausted(f"mcp-miss:{ip}", limit) is True  # noqa: SLF001
    # exhausted() never adds an event; another IP is untouched.
    assert rate_limit._LIMITER.exhausted("mcp-miss:198.51.100.1", limit) is False  # noqa: SLF001


def test_limit_is_per_token():
    for _ in range(3):
        assert rate_limit.check_mcp_token_rate_limit("hash-a", requests_per_minute=3) is True
    assert rate_limit.check_mcp_token_rate_limit("hash-a", requests_per_minute=3) is False
    # Another token is unaffected.
    assert rate_limit.check_mcp_token_rate_limit("hash-b", requests_per_minute=3) is True
