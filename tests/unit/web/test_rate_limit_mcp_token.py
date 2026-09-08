"""Spec #78 Phase 2 — per-token HTTP request limit primitive."""

import pytest

from thestill.web.middleware import rate_limit


@pytest.fixture(autouse=True)
def _reset():
    rate_limit.reset_for_testing()
    yield
    rate_limit.reset_for_testing()


def test_limit_is_per_token():
    for _ in range(3):
        assert rate_limit.check_mcp_token_rate_limit("hash-a", requests_per_minute=3) is True
    assert rate_limit.check_mcp_token_rate_limit("hash-a", requests_per_minute=3) is False
    # Another token is unaffected.
    assert rate_limit.check_mcp_token_rate_limit("hash-b", requests_per_minute=3) is True
