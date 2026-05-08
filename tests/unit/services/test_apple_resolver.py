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

"""Unit tests for ``ApplePodcastsResolver``.

The resolver is tested against canned iTunes Search API responses so the
suite stays offline.
"""

from datetime import datetime, timezone

import pytest

from thestill.services.import_service import (
    ApplePodcastsResolver,
    CanonicalParent,
    ResolverError,
)


_APPLE_EPISODE_URL = (
    "https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000"
)


def _resolver_with(info):
    return ApplePodcastsResolver(episode_lookup=lambda track_id: info)


# ============================================================================
# matches()
# ============================================================================


@pytest.mark.parametrize(
    "url,expected",
    [
        (_APPLE_EPISODE_URL, True),
        ("https://podcasts.apple.com/us/podcast/foo/id123?i=456", True),
        ("https://podcasts.apple.com/gb/podcast/foo/id123", True),  # show only — match() yes, resolve() no
        ("https://www.youtube.com/watch?v=abc", False),
        ("https://example.com/foo.mp3", False),
        ("https://music.apple.com/us/album/xyz/id1", False),  # different host
    ],
)
def test_matches(url, expected):
    assert ApplePodcastsResolver().matches(url) is expected


# ============================================================================
# resolve() — canonical mapping
# ============================================================================


def test_resolve_maps_itunes_fields_to_canonical_source(fake_apple_episode_info):
    src = _resolver_with(fake_apple_episode_info).resolve(_APPLE_EPISODE_URL)

    assert src.kind == "rss_episode"
    assert src.canonical_id == "apple:1000620312000"
    assert src.external_id == "1000620312000"
    assert src.audio_url == "https://cdn.example.com/episode.mp3"
    assert src.title == "The Friday News Roundup"
    assert src.description == "A summary of the week."
    assert src.duration_seconds == 1800
    assert src.pub_date == datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
    assert src.image_url == "https://cdn.example.com/cover-600.jpg"
    assert src.source_handle == "The Daily"


def test_resolve_emits_canonical_parent_for_show(fake_apple_episode_info):
    src = _resolver_with(fake_apple_episode_info).resolve(_APPLE_EPISODE_URL)

    assert isinstance(src.parent, CanonicalParent)
    # Show id comes from the URL path, not the lookup payload — that way a
    # mismatched lookup can't silently corrupt the parent linkage.
    assert src.parent.external_id == "1200361736"
    assert src.parent.title == "The Daily"
    assert src.parent.rss_url == "https://feeds.example.com/the-daily"
    assert src.parent.image_url == "https://cdn.example.com/cover-600.jpg"


def test_resolve_falls_back_to_artwork100_when_600_missing(fake_apple_episode_info):
    info = {**fake_apple_episode_info}
    info.pop("artworkUrl600")
    src = _resolver_with(info).resolve(_APPLE_EPISODE_URL)
    assert src.image_url == "https://cdn.example.com/cover-100.jpg"


def test_resolve_handles_missing_release_date(fake_apple_episode_info):
    info = {**fake_apple_episode_info}
    info.pop("releaseDate")
    src = _resolver_with(info).resolve(_APPLE_EPISODE_URL)
    assert src.pub_date is None


def test_resolve_omits_parent_when_feed_url_missing(fake_apple_episode_info):
    info = {**fake_apple_episode_info}
    info.pop("feedUrl")
    src = _resolver_with(info).resolve(_APPLE_EPISODE_URL)
    assert src.parent is None


def test_resolve_falls_back_to_preview_url_when_episode_url_missing(fake_apple_episode_info):
    info = {**fake_apple_episode_info}
    info.pop("episodeUrl")
    info["previewUrl"] = "https://cdn.example.com/preview.mp3"
    src = _resolver_with(info).resolve(_APPLE_EPISODE_URL)
    assert src.audio_url == "https://cdn.example.com/preview.mp3"


# ============================================================================
# Error paths
# ============================================================================


def test_resolve_rejects_show_only_link():
    """Apple URLs without ``?i=<track_id>`` point at a show, not an episode."""
    resolver = ApplePodcastsResolver(episode_lookup=lambda tid: {})
    with pytest.raises(ResolverError) as exc_info:
        resolver.resolve("https://podcasts.apple.com/us/podcast/the-daily/id1200361736")
    assert "single episode" in str(exc_info.value)


def test_resolve_raises_when_audio_url_missing(fake_apple_episode_info):
    info = {**fake_apple_episode_info}
    info.pop("episodeUrl")
    info.pop("previewUrl", None)
    with pytest.raises(ResolverError) as exc_info:
        _resolver_with(info).resolve(_APPLE_EPISODE_URL)
    assert "audio URL" in str(exc_info.value)
