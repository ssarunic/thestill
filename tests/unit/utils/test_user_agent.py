"""Coarse user-agent classes for public page views (spec #95)."""

import pytest

from thestill.utils.user_agent import classify_user_agent

CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


@pytest.mark.parametrize(
    ("ua", "cls"),
    [
        (CHROME, "browser"),
        (IPHONE, "browser"),
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "bot"),
        ("Mozilla/5.0 (compatible; bingbot/2.0)", "bot"),
        ("python-requests/2.32", "bot"),
        ("curl/8.4.0", "bot"),
        ("Mozilla/5.0 HeadlessChrome/129.0", "bot"),
        ("Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "preview"),
        ("facebookexternalhit/1.1", "preview"),
        ("Twitterbot/1.0", "preview"),
        ("WhatsApp/2.23", "preview"),
        ("", "bot"),
        (None, "bot"),
    ],
)
def test_classify_user_agent(ua, cls):
    assert classify_user_agent(ua) == cls
