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

"""Regression tests for spec #25 item 4.3 — centralised URL patterns + ReDoS guard.

The `time.monotonic` deadline below is wall-clock; CI runners are noisy
enough that we set it generously (500 ms per pattern × pathological
input). A real ReDoS would blow past this by orders of magnitude — the
classic ``(a+)+`` against a 50-char input takes seconds; we evaluate
against ~10 KB which would push that into hours.
"""

from __future__ import annotations

import time

import pytest

from thestill.utils.url_patterns import (
    ALL_PATTERNS,
    APPLE_PODCAST_ID_RE,
    extract_apple_podcast_id,
    is_youtube_url,
    looks_like_rss,
)

# Pathological inputs designed to stress regexes that have catastrophic
# backtracking. If a pattern survives all of these in <500 ms, it is
# safe for production input where attacker-controlled URLs are at most
# a few KB.
_PATHOLOGICAL_INPUTS = [
    "a" * 10_000,
    "0" * 10_000,
    ("ab" * 5_000),
    "youtube.com/" + ("a" * 9_500),
    "/feed/" + ("/feed" * 1_000),
    "id" + ("9" * 10_000),
    ("id1" * 5_000),
]

_DEADLINE_SECONDS = 0.5


@pytest.mark.parametrize("pattern", ALL_PATTERNS, ids=lambda p: p.pattern)
@pytest.mark.parametrize("inp", _PATHOLOGICAL_INPUTS, ids=lambda s: f"len={len(s)}")
def test_pattern_terminates_on_pathological_input(pattern, inp):
    start = time.monotonic()
    pattern.search(inp)
    elapsed = time.monotonic() - start
    assert elapsed < _DEADLINE_SECONDS, (
        f"pattern {pattern.pattern!r} took {elapsed:.3f}s on input " f"len={len(inp)} — possible ReDoS"
    )


# ---------------------------------------------------------------------------
# Behavioural sanity — these must not regress when the patterns are tweaked
# ---------------------------------------------------------------------------


class TestIsYoutubeUrl:
    def test_video_url(self):
        assert is_youtube_url("https://www.youtube.com/watch?v=abc")

    def test_short_url(self):
        assert is_youtube_url("https://youtu.be/abc")

    def test_channel_handle(self):
        assert is_youtube_url("https://www.youtube.com/@somechannel")

    def test_playlist(self):
        assert is_youtube_url("https://www.youtube.com/playlist?list=PL1")

    def test_non_youtube(self):
        assert not is_youtube_url("https://example.com/feed.xml")

    def test_empty(self):
        assert not is_youtube_url("")


class TestLooksLikeRss:
    def test_xml_suffix(self):
        assert looks_like_rss("https://example.com/feed.xml")

    def test_xml_suffix_uppercase(self):
        assert looks_like_rss("https://example.com/FEED.XML")

    def test_feed_path(self):
        assert looks_like_rss("https://example.com/feed")

    def test_feed_path_trailing_slash(self):
        assert looks_like_rss("https://example.com/feed/")

    def test_rss_suffix(self):
        assert looks_like_rss("https://example.com/somefeed.rss")

    def test_random_url(self):
        assert not looks_like_rss("https://example.com/podcast-show/episode-1")

    def test_youtube_url(self):
        assert not looks_like_rss("https://youtu.be/abc")


class TestExtractApplePodcastId:
    def test_url_with_id(self):
        assert extract_apple_podcast_id("https://podcasts.apple.com/podcast/x/id1234567890") == "1234567890"

    def test_no_match(self):
        assert extract_apple_podcast_id("https://example.com/no-id-here") is None

    def test_returns_only_first(self):
        # First match wins; trailing IDs are ignored.
        assert extract_apple_podcast_id("id1 then id2 then id3") == "1"

    def test_id_bound_caps_at_12_digits(self):
        """The 12-digit upper bound prevents DoS via massive numeric inputs."""
        # 13 digits in a row → only the first 12 are captured.
        result = APPLE_PODCAST_ID_RE.search("id" + "9" * 13)
        assert result is not None
        assert len(result.group(1)) == 12
