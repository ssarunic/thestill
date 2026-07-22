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

"""Tests for RSSMediaSource.extract_alternate_enclosures (Podcasting 2.0)."""

import pytest

from thestill.core.media_source import RSSMediaSource


@pytest.fixture
def source():
    return RSSMediaSource()


def _wrap(items_xml: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Test Feed</title>
  {items_xml}
</channel>
</rss>"""


def test_no_alternate_enclosures_returns_empty(source):
    rss = _wrap(
        """<item>
            <title>Audio only</title>
            <guid>ep-001</guid>
            <enclosure url="https://x.com/ep1.mp3" type="audio/mpeg" length="100"/>
        </item>"""
    )
    assert source.extract_alternate_enclosures(rss) == {}


def test_extracts_hls_alternate_enclosure(source):
    rss = _wrap(
        """<item>
            <title>HLS video</title>
            <guid>ep-hls</guid>
            <enclosure url="https://x.com/ep.mp3" type="audio/mpeg"/>
            <podcast:alternateEnclosure type="application/x-mpegURL" height="1080"
                                        bitrate="3500000" default="true">
                <podcast:source uri="https://cdn.x.com/ep/master.m3u8"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    result = source.extract_alternate_enclosures(rss)
    assert set(result.keys()) == {"ep-hls"}
    entries = result["ep-hls"]
    assert len(entries) == 1
    e = entries[0]
    assert e.source_uri == "https://cdn.x.com/ep/master.m3u8"
    assert e.mime_type == "application/x-mpegURL"
    assert e.height == 1080
    assert e.bitrate == 3500000.0
    assert e.is_default is True


def test_multiple_sources_emit_one_row_each(source):
    """An alternateEnclosure with N <podcast:source> children → N rows sharing parent metadata."""
    rss = _wrap(
        """<item>
            <title>MP4 with WebM fallback</title>
            <guid>ep-mp4</guid>
            <podcast:alternateEnclosure type="video/mp4" height="720" bitrate="2000000">
                <podcast:source uri="https://cdn.x.com/ep/720p.mp4"/>
                <podcast:source uri="https://cdn.x.com/ep/720p.webm"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    entries = source.extract_alternate_enclosures(rss)["ep-mp4"]
    assert len(entries) == 2
    uris = sorted(e.source_uri for e in entries)
    assert uris == [
        "https://cdn.x.com/ep/720p.mp4",
        "https://cdn.x.com/ep/720p.webm",
    ]
    for e in entries:
        assert e.mime_type == "video/mp4"
        assert e.height == 720
        assert e.bitrate == 2000000.0
        assert e.is_default is False


def test_video_youtube_pointer_extracted(source):
    """Captivate-style: type=video/youtube with a YouTube URL in <podcast:source>."""
    rss = _wrap(
        """<item>
            <title>YT episode</title>
            <guid>ep-yt</guid>
            <podcast:alternateEnclosure type="video/youtube">
                <podcast:source uri="https://youtu.be/abc123"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    entries = source.extract_alternate_enclosures(rss)["ep-yt"]
    assert len(entries) == 1
    assert entries[0].mime_type == "video/youtube"
    assert entries[0].source_uri == "https://youtu.be/abc123"


def test_missing_type_skips_entry(source):
    rss = _wrap(
        """<item>
            <title>Bogus alt</title>
            <guid>ep-bogus</guid>
            <podcast:alternateEnclosure>
                <podcast:source uri="https://x.com/file"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    assert source.extract_alternate_enclosures(rss) == {}


def test_missing_source_uri_skipped(source):
    rss = _wrap(
        """<item>
            <title>No source uri</title>
            <guid>ep-nouri</guid>
            <podcast:alternateEnclosure type="video/mp4"/>
        </item>"""
    )
    assert source.extract_alternate_enclosures(rss) == {}


def test_multiline_guid_is_stripped_to_match_feedparser(source):
    """Feedparser trims element text before it becomes external_id; a
    pretty-printed <guid> must key identically or the batch INSERT..SELECT
    silently drops the episode's rows."""
    rss = _wrap(
        """<item>
            <title>Formatted guid</title>
            <guid isPermaLink="false">
                ep-padded
            </guid>
            <podcast:alternateEnclosure type="video/youtube">
                <podcast:source uri="https://youtu.be/paddedVID01"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    result = source.extract_alternate_enclosures(rss)
    assert set(result.keys()) == {"ep-padded"}


def test_whitespace_only_guid_falls_back_to_id(source):
    rss = _wrap(
        """<item>
            <title>Blank guid</title>
            <guid>   </guid>
            <id>atom-fallback-1</id>
            <podcast:alternateEnclosure type="video/youtube">
                <podcast:source uri="https://youtu.be/fallbkVID01"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    assert set(source.extract_alternate_enclosures(rss).keys()) == {"atom-fallback-1"}


def test_falls_back_to_id_when_guid_missing(source):
    rss = _wrap(
        """<item>
            <title>Atom-style</title>
            <id>atom-id-1</id>
            <podcast:alternateEnclosure type="application/x-mpegURL">
                <podcast:source uri="https://x.com/master.m3u8"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    assert set(source.extract_alternate_enclosures(rss).keys()) == {"atom-id-1"}


def test_invalid_numeric_attrs_become_none(source):
    rss = _wrap(
        """<item>
            <title>Garbage attrs</title>
            <guid>ep-bad</guid>
            <podcast:alternateEnclosure type="video/mp4" height="HD" length="big" bitrate="abc">
                <podcast:source uri="https://x.com/ep.mp4"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    e = source.extract_alternate_enclosures(rss)["ep-bad"][0]
    assert e.height is None
    assert e.length is None
    assert e.bitrate is None


def test_malformed_xml_returns_empty(source):
    assert source.extract_alternate_enclosures("<<<not xml>>>") == {}


def test_default_flag_case_insensitive(source):
    rss = _wrap(
        """<item>
            <title>Default true variants</title>
            <guid>ep-default</guid>
            <podcast:alternateEnclosure type="video/mp4" default="TRUE">
                <podcast:source uri="https://x.com/a.mp4"/>
            </podcast:alternateEnclosure>
            <podcast:alternateEnclosure type="video/mp4" default=" true ">
                <podcast:source uri="https://x.com/b.mp4"/>
            </podcast:alternateEnclosure>
            <podcast:alternateEnclosure type="video/mp4" default="false">
                <podcast:source uri="https://x.com/c.mp4"/>
            </podcast:alternateEnclosure>
            <podcast:alternateEnclosure type="video/mp4">
                <podcast:source uri="https://x.com/d.mp4"/>
            </podcast:alternateEnclosure>
        </item>"""
    )
    entries = sorted(
        source.extract_alternate_enclosures(rss)["ep-default"],
        key=lambda e: e.source_uri,
    )
    flags = [e.is_default for e in entries]
    assert flags == [True, True, False, False]
