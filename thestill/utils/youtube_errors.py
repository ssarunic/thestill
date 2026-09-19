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

"""Human-readable descriptions for well-known yt-dlp failures.

yt-dlp surfaces YouTube's own error text verbatim, padded with CLI advice
(``Use --cookies-from-browser ...``) that means nothing to a web user.
Both the import resolver and the download stage route their yt-dlp
exceptions through :func:`describe_youtube_failure` so the user sees what
went wrong and what they can do about it.
"""

import re
from typing import Optional

YOUTUBE_BOT_CHECK_MESSAGE = (
    "YouTube is blocking automated access from this server "
    '("Sign in to confirm you\'re not a bot"). This is YouTube rate-limiting '
    "the server's IP address, not a problem with the video. Try again later, "
    "or paste a direct audio link (.mp3, .m4a, .opus, .ogg, .wav) instead."
)

_YT_FAILURE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sign in to confirm you.re not a bot", YOUTUBE_BOT_CHECK_MESSAGE),
    (
        r"private video|this video is private",
        "This YouTube video is private, so it can't be imported.",
    ),
    (
        r"sign in to confirm your age|age.restricted|inappropriate for some users",
        "This YouTube video is age-restricted, which requires a signed-in account to fetch.",
    ),
    (
        r"available in your country|geo.?restrict|blocked it in your country",
        "This YouTube video is not available in the server's region.",
    ),
    (
        r"members.only|join this channel",
        "This YouTube video is members-only, so it can't be imported.",
    ),
    (
        r"live event|live stream|premieres? in",
        "This YouTube video is a live stream or premiere that hasn't finished yet. Import it once the recording is available.",
    ),
    # Generic last: yt-dlp prefixes specific causes with "Video unavailable."
    # ("Video unavailable. This content is age-restricted."), so a generic
    # match first would hide the actionable reason.
    (
        r"video unavailable|has been removed|no longer available|does not exist",
        "This YouTube video is unavailable: it may have been removed or the link is wrong.",
    ),
)


def describe_youtube_failure(error: BaseException) -> Optional[str]:
    """Map a raw yt-dlp error onto a human-readable, actionable message.

    yt-dlp raises ``DownloadError`` with the extractor's message inline, e.g.
    ``ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use
    --cookies-from-browser ...``. Users shouldn't see CLI flag advice, so
    the well-known failures are rewritten here. Returns ``None`` when the
    error isn't recognised, in which case callers fall back to the raw text.
    """
    text = str(error)
    for pattern, message in _YT_FAILURE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return message
    return None
