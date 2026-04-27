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

"""Centralised URL patterns (spec #25 item 4.3).

Every URL classification or extraction regex used by the core pipeline
lives here, pre-compiled. Centralising them serves three goals:

1. **One place to audit** — every pattern can be eyeballed alongside its
   neighbours, making it easy to spot ReDoS-prone constructs (unbounded
   alternation, nested quantifiers, backreferences).
2. **Pre-compile cost paid once** — call sites that previously rebuilt
   the regex on every invocation now hit a hot-path compiled object.
3. **Regression surface for tests** — the test suite can iterate over a
   well-known list of patterns and assert each one terminates in
   bounded time against pathological input.

Rules for new patterns:

- Avoid alternation over groups that overlap (``(a|aa)+``).
- Bound quantifiers when the input length is known.
- No backreferences (``\\1``) — they push regex into NP territory.
- Use raw strings.
- Add a unit test in ``tests/unit/security/test_url_patterns.py``.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

# Each substring is anchored on a literal token (``youtube.com/`` or
# ``youtu.be/``) followed by a small fixed-shape suffix; no nested
# quantifiers, no alternation over overlapping groups.
YOUTUBE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"youtube\.com/watch"),
    re.compile(r"youtube\.com/playlist"),
    re.compile(r"youtube\.com/@[\w-]+"),
    re.compile(r"youtube\.com/channel/"),
    re.compile(r"youtube\.com/c/"),
    re.compile(r"youtu\.be/"),
)


def is_youtube_url(url: str) -> bool:
    """Return True iff ``url`` looks like a YouTube video, channel, or playlist."""
    return any(p.search(url) for p in YOUTUBE_PATTERNS)


# ---------------------------------------------------------------------------
# RSS / Podcast feed shape hints
# ---------------------------------------------------------------------------
#
# These are weak hints used to short-circuit feed-vs-website detection
# before any network request — they MUST NOT be the only validation step
# (see ``utils.url_guard`` for the actual safety check). All anchored at
# the end of the URL or on a fixed-character separator; no alternation
# over the whole pattern.

RSS_HINT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\.xml$", re.IGNORECASE),
    re.compile(r"\.rss$", re.IGNORECASE),
    re.compile(r"/feed/?$", re.IGNORECASE),
    re.compile(r"/rss/?$", re.IGNORECASE),
    re.compile(r"/podcast/?$", re.IGNORECASE),
)


def looks_like_rss(url: str) -> bool:
    """Return True iff ``url`` looks like an RSS feed by URL shape alone."""
    return any(p.search(url) for p in RSS_HINT_PATTERNS)


# ---------------------------------------------------------------------------
# Apple Podcasts numeric ID
# ---------------------------------------------------------------------------

# Apple URLs embed the show ID as ``/id<digits>`` in the path. The bound
# of 12 digits is generous (Apple IDs are currently 10) and prevents a
# pathological digit-only input from forcing the regex engine to chew
# through a multi-megabyte numeric string.
APPLE_PODCAST_ID_RE: Final[re.Pattern[str]] = re.compile(r"id(\d{1,12})")


def extract_apple_podcast_id(text: str) -> str | None:
    """Return the first ``id<digits>`` match in ``text``, or None."""
    match = APPLE_PODCAST_ID_RE.search(text)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

# Single source of truth for "every pattern this module owns" — the
# ReDoS regression tests iterate over this rather than maintaining their
# own list.
ALL_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    *YOUTUBE_PATTERNS,
    *RSS_HINT_PATTERNS,
    APPLE_PODCAST_ID_RE,
)
