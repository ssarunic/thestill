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
    extract_apple_episode_id,
    extract_apple_podcast_id,
    extract_spotify_entity,
    is_apple_podcast_url,
    is_spotify_url,
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

    def test_shorts_url(self):
        assert is_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")

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


class TestExtractAppleEpisodeId:
    def test_url_with_episode_query(self):
        url = "https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000"
        assert extract_apple_episode_id(url) == "1000620312000"

    def test_url_without_i_param(self):
        url = "https://podcasts.apple.com/us/podcast/the-daily/id1200361736"
        assert extract_apple_episode_id(url) is None

    def test_picks_only_the_i_param_not_other_digits(self):
        url = "https://podcasts.apple.com/us/podcast/x/id123?other=999&i=42"
        assert extract_apple_episode_id(url) == "42"


class TestIsApplePodcastUrl:
    def test_canonical_share_link(self):
        assert is_apple_podcast_url("https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000")

    def test_show_only_link_still_classifies_as_apple(self):
        # Resolver rejects show-only links separately; the URL classifier
        # only cares about the host.
        assert is_apple_podcast_url("https://podcasts.apple.com/us/podcast/foo/id123")

    def test_apple_music_is_not_apple_podcasts(self):
        assert not is_apple_podcast_url("https://music.apple.com/us/album/foo/id1")

    def test_youtube_is_not_apple(self):
        assert not is_apple_podcast_url("https://www.youtube.com/watch?v=abc")


class TestSpotifyPatterns:
    _ID = "4rOoJ6Egrf8K2IrywzwOMk"

    def test_episode_and_show_links(self):
        assert extract_spotify_entity(f"https://open.spotify.com/episode/{self._ID}?si=abc") == ("episode", self._ID)
        assert extract_spotify_entity(f"https://open.spotify.com/show/{self._ID}") == ("show", self._ID)

    def test_locale_prefixes(self):
        assert extract_spotify_entity(f"https://open.spotify.com/intl-de/episode/{self._ID}") == ("episode", self._ID)
        assert extract_spotify_entity(f"https://open.spotify.com/pt-br/show/{self._ID}") == ("show", self._ID)

    def test_uri_form(self):
        assert extract_spotify_entity(f"spotify:episode:{self._ID}") == ("episode", self._ID)
        assert extract_spotify_entity(f"  spotify:show:{self._ID}\n") == ("show", self._ID)

    def test_rejects_wrong_length_or_other_entities(self):
        assert extract_spotify_entity("https://open.spotify.com/episode/tooshort") is None
        assert extract_spotify_entity(f"https://open.spotify.com/episode/{self._ID}X") is None
        assert extract_spotify_entity("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") is None
        assert extract_spotify_entity("https://spotify.link/AbC123") is None

    def test_is_spotify_url(self):
        assert is_spotify_url(f"https://open.spotify.com/episode/{self._ID}")
        assert is_spotify_url("https://spotify.link/AbC123")
        assert is_spotify_url(f"spotify:episode:{self._ID}")
        assert not is_spotify_url("https://notspotify.com/episode/x")
        assert is_spotify_url(f"open.spotify.com/show/{self._ID}")  # scheme-less paste

    def test_spotify_host_is_matched_on_the_parsed_host_not_a_substring(self):
        lookalikes = [
            f"https://evil.example/x?next=open.spotify.com/episode/{self._ID}",
            f"https://feeds.example.com/rss?ref=open.spotify.com/show/{self._ID}",
            f"https://open.spotify.com.evil.example/episode/{self._ID}",
            "https://notspotify.link/AbC123",
            "https://evil.example/spotify.link/AbC123",
        ]
        for url in lookalikes:
            assert not is_spotify_url(url), url
            assert extract_spotify_entity(url) is None, url
        assert not is_spotify_url("https://podcasts.apple.com/us/podcast/x/id1?i=2")


class TestSpec87TextScanners:
    """Spec #87 — link scanners over free text (descriptions, show notes)."""

    def test_youtube_video_ids_in_text(self):
        from thestill.utils.url_patterns import find_youtube_video_ids

        text = (
            'see <a href="https://www.youtube.com/watch?feature=share&v=s7d2d8FhevU">x</a>, '
            "youtu.be/NYFGCESmikA?t=1, youtube.com/live/l6USUAIKJls and a 12-char run youtube.com/watch?v=s7d2d8FhevUX"
        )
        assert find_youtube_video_ids(text) == ["s7d2d8FhevU", "NYFGCESmikA", "l6USUAIKJls"]
        assert find_youtube_video_ids("") == []

    def test_youtube_channel_urls_in_text(self):
        from thestill.utils.url_patterns import find_youtube_channel_urls

        text = "https://www.youtube.com/@lexfridman/videos youtube.com/channel/UCSHZKyawb77ixDdsGog4iWA youtube.com/watch?v=aaaaaaaaaaa"
        assert find_youtube_channel_urls(text) == [
            "https://www.youtube.com/@lexfridman",
            "https://www.youtube.com/channel/UCSHZKyawb77ixDdsGog4iWA",
        ]

    def test_apple_show_links_in_text(self):
        from thestill.utils.url_patterns import find_apple_show_links

        text = (
            'Listen on <a href="https://podcasts.apple.com/us/podcast/a16z-podcast/id842818711?i=1000792176970">Apple</a> '
            "podcasts.apple.com/gb/podcast/id842818711 podcasts.apple.com/podcast/id123456 "
            "https://music.apple.com/us/album/id999 podcasts.apple.com/us/podcast/x/id1234567890123"
        )
        assert find_apple_show_links(text) == [
            ("842818711", "https://podcasts.apple.com/us/podcast/a16z-podcast/id842818711"),
            ("123456", "https://podcasts.apple.com/podcast/id123456"),
        ]
        assert find_apple_show_links("") == []

    def test_spotify_entities_in_text(self):
        from thestill.utils.url_patterns import find_spotify_entities

        text = "https://open.spotify.com/episode/0tkEdaVIsQNGKUGUKTPqeH?si=x https://open.spotify.com/intl-de/show/2MAi0BvDc6GTFvKFPXnkCL"
        assert find_spotify_entities(text) == [
            ("episode", "0tkEdaVIsQNGKUGUKTPqeH"),
            ("show", "2MAi0BvDc6GTFvKFPXnkCL"),
        ]
