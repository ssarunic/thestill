"""Coarse user-agent classes for public page views (spec #95)."""

import pytest

from thestill.utils.user_agent import classify_user_agent, crawler_family

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


@pytest.mark.parametrize(
    ("ua", "family"),
    [
        ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "googlebot"),
        ("Mozilla/5.0 (compatible; Google-InspectionTool/1.0)", "googlebot"),
        ("Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "bingbot"),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)",
            "oai-searchbot",
        ),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot",
            "chatgpt-user",
        ),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot",
            "gptbot",
        ),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
            "claudebot",
        ),
        ("Mozilla/5.0 (compatible; Claude-User/1.0; +Claude-User@anthropic.com)", "claude-user"),
        ("Mozilla/5.0 (compatible; Claude-SearchBot/1.0; +searchbot@anthropic.com)", "claude-searchbot"),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
            "perplexitybot",
        ),
        (
            "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0; +https://perplexity.ai/perplexity-user)",
            "perplexity-user",
        ),
        ("CCBot/2.0 (https://commoncrawl.org/faq/)", "ccbot"),
        (
            "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
            "meta-externalagent",
        ),
        (
            "Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 (compatible; Bytespider; spider-feedback@bytedance.com)",
            "bytespider",
        ),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.1.1 Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)",
            "applebot",
        ),
        ("DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)", "duckduckbot"),
        ("python-requests/2.32", "other-bot"),
        (None, "other-bot"),
        ("Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "preview"),
        (CHROME, "browser"),
    ],
)
def test_crawler_family(ua, family):
    assert crawler_family(ua) == family
