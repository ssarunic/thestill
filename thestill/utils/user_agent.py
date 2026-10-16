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

"""Coarse user-agent class for public page views (spec #94 §7, #95).

``preview`` is a link unfurler (a chat app fetching a card), ``bot`` a
crawler or HTTP library, ``browser`` everything else. Deliberately coarse:
it only decides whether a view is a person, never who.
"""

import re
from typing import Literal, Optional

UaClass = Literal["browser", "bot", "preview"]

_PREVIEW = re.compile(
    r"slackbot|twitterbot|facebookexternalhit|facebot|linkedinbot|discordbot|telegrambot|whatsapp"
    r"|skypeuripreview|iframely|embedly|applebot|imessage|redditbot|pinterest|mastodon",
    re.I,
)
_BOT = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|bingpreview|headless|lighthouse|python-|curl/|wget/|httpx|go-http"
    r"|java/|okhttp|axios|node-fetch|libwww|scrapy|feedfetcher|monitor|uptime",
    re.I,
)


def classify_user_agent(user_agent: Optional[str]) -> UaClass:
    """``browser``, ``bot`` or ``preview``; a missing user agent is a bot."""
    if not user_agent:
        return "bot"
    if _PREVIEW.search(user_agent):
        return "preview"
    if _BOT.search(user_agent):
        return "bot"
    return "browser"
