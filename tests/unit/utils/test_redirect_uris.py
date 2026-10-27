"""Spec #109 — the redirect URI policy and loopback checks."""

import pytest

from thestill.utils.redirect_uris import is_allowed_redirect_uri, is_loopback_uri


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "https://chatgpt.com/connector/oauth/abc",
        "http://localhost/callback",
        "http://127.0.0.1:53121/callback",
        "http://[::1]:8080/cb",
        "com.example.app:/oauth2redirect",
        "cursor://anysphere.cursor-retrieval/oauth/callback",
    ],
)
def test_allowed(uri):
    assert is_allowed_redirect_uri(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)//",
        "JavaScript:alert(1)",
        "data:text/html,<script>",
        "vbscript:msgbox",
        "file:///etc/passwd",
        "http://evil.example/cb",
        "http://localhost.evil.example/cb",
        "https://claude.ai/cb#frag",
        "https://user:pw@claude.ai/cb",
        "https:///nohost",
        "wss://x.example/cb",
        "not a uri",
        "http://[::1/cb",
    ],
)
def test_refused(uri):
    assert not is_allowed_redirect_uri(uri)


def test_loopback_uri():
    assert is_loopback_uri("http://localhost:8000")
    assert not is_loopback_uri("http://localhost.evil.example")
    assert not is_loopback_uri("https://localhost")
