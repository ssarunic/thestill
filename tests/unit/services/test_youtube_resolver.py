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

"""Unit tests for ``YouTubeResolver``.

The resolver is tested against canned yt-dlp metadata so the suite stays
offline. The download stage's existing yt-dlp integration is what fetches
audio at runtime; we only need to verify the metadata mapping here.
"""

from datetime import datetime

import pytest

from thestill.services.import_service import (
    CanonicalParent,
    ResolverError,
    YouTubeResolver,
)


def _resolver_with(info):
    return YouTubeResolver(metadata_fetcher=lambda url: info)


# ============================================================================
# matches()
# ============================================================================


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://m.youtube.com/watch?v=abc", True),
        ("https://youtu.be/abc", True),
        ("https://www.youtube.com/shorts/abc", True),
        ("https://www.youtube.com/playlist?list=PL1", True),
        ("https://example.com/foo.mp3", False),
        ("https://vimeo.com/123", False),
    ],
)
def test_matches(url, expected):
    assert YouTubeResolver().matches(url) is expected


# ============================================================================
# resolve() — canonical mapping
# ============================================================================


def test_resolve_maps_yt_dlp_fields_to_canonical_source(fake_youtube_video_info):
    src = _resolver_with(fake_youtube_video_info).resolve(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )

    assert src.kind == "youtube"
    assert src.canonical_id == "youtube:dQw4w9WgXcQ"
    assert src.external_id == "dQw4w9WgXcQ"
    assert src.audio_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert src.title == "Never Gonna Give You Up"
    assert src.description == "Music video"
    assert src.duration_seconds == 213
    assert src.pub_date == datetime(2009, 10, 25)
    # The largest thumbnail (last in the list) is preferred.
    assert src.image_url == "https://i.ytimg.com/hi.jpg"
    assert src.source_handle == "Rick Astley"


def test_resolve_emits_canonical_parent_for_channel(fake_youtube_video_info):
    src = _resolver_with(fake_youtube_video_info).resolve(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )

    assert isinstance(src.parent, CanonicalParent)
    assert src.parent.external_id == "UCuAXFkgsw1L7xaCfnd5JJOw"
    assert src.parent.title == "Rick Astley"
    assert src.parent.rss_url == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCuAXFkgsw1L7xaCfnd5JJOw"
    )


def test_resolve_falls_back_to_thumbnail_when_thumbnails_missing(fake_youtube_video_info):
    info = {**fake_youtube_video_info, "thumbnails": [], "thumbnail": "https://i.ytimg.com/single.jpg"}
    src = _resolver_with(info).resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert src.image_url == "https://i.ytimg.com/single.jpg"


def test_resolve_handles_missing_pub_date(fake_youtube_video_info):
    info = {**fake_youtube_video_info}
    info.pop("upload_date")
    info.pop("timestamp", None)
    src = _resolver_with(info).resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert src.pub_date is None


def test_resolve_omits_parent_when_channel_id_is_missing(fake_youtube_video_info):
    info = {**fake_youtube_video_info}
    info.pop("channel_id")
    info["uploader_id"] = "@someuser"  # not a UC... id, must NOT become a parent
    src = _resolver_with(info).resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert src.parent is None


def test_resolve_falls_back_to_uploader_id_when_it_is_a_channel_id(fake_youtube_video_info):
    info = {**fake_youtube_video_info}
    info.pop("channel_id")
    info["uploader_id"] = "UCabc123def456ghi789jkl"
    src = _resolver_with(info).resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert src.parent is not None
    assert src.parent.external_id == "UCabc123def456ghi789jkl"


def test_resolve_raises_when_id_missing(fake_youtube_video_info):
    info = {**fake_youtube_video_info}
    info.pop("id")
    with pytest.raises(ResolverError):
        _resolver_with(info).resolve("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
