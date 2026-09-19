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

"""Unit tests for ``thestill.utils.youtube_errors``."""

import pytest

from thestill.utils.youtube_errors import YOUTUBE_BOT_CHECK_MESSAGE, describe_youtube_failure

# Verbatim yt-dlp text observed in production (note the curly apostrophe).
_BOT_CHECK = (
    "ERROR: [youtube] ZNtevnJe_8k: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
)


def test_bot_check_is_rewritten_without_cli_advice():
    message = describe_youtube_failure(Exception(_BOT_CHECK))
    assert message == YOUTUBE_BOT_CHECK_MESSAGE
    assert "--cookies" not in message
    assert "direct audio link" in message


def test_bot_check_matches_straight_apostrophe_too():
    assert describe_youtube_failure(Exception("Sign in to confirm you're not a bot")) == YOUTUBE_BOT_CHECK_MESSAGE


@pytest.mark.parametrize(
    "raw,fragment",
    [
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", "private"),
        ("ERROR: [youtube] abc: Video unavailable", "unavailable"),
        ("ERROR: [youtube] abc: Sign in to confirm your age", "age-restricted"),
        ("ERROR: [youtube] abc: The uploader has not made this video available in your country", "region"),
        ("ERROR: [youtube] abc: Join this channel to get access to members-only content", "members-only"),
        ("ERROR: [youtube] abc: This live event will begin in 2 hours", "live stream"),
        # yt-dlp prefixes specific causes with the generic phrase; the
        # specific reason must win or the user is told to check the link.
        ("ERROR: [youtube] abc: Video unavailable. This content is age-restricted.", "age-restricted"),
        ("ERROR: [youtube] abc: Video unavailable. This video is private.", "private"),
        (
            "ERROR: [youtube] abc: Video unavailable. The uploader has not made this video available in your country",
            "region",
        ),
        (
            "ERROR: [youtube] abc: Video unavailable. Join this channel to get access to members-only content",
            "members-only",
        ),
    ],
)
def test_known_failures_get_specific_messages(raw, fragment):
    message = describe_youtube_failure(Exception(raw))
    assert message is not None
    assert fragment in message
    assert "ERROR:" not in message


def test_unknown_failure_returns_none():
    assert describe_youtube_failure(Exception("ERROR: something new and strange")) is None
