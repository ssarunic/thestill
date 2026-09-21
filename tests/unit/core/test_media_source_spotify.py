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

"""Spotify show links in the add-podcast path (spec #79).

``RSSMediaSource`` resolves a Spotify show (or episode) URL to the show's
RSS feed through ``SpotifyLinkResolver`` before the normal feed fetch, the
same way it already resolves Apple Podcasts URLs.
"""

import pytest

from thestill.core.media_source import FetchRSSResult, RSSMediaSource
from thestill.core.spotify_resolver import AppleShowMatch, SpotifyResolutionError

_SHOW_URL = "https://open.spotify.com/show/2aB3cD4eF5gH6iJ7kL8mN9"
_FEED_URL = "https://feeds.example.com/sources"

_FEED_XML = """<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel>
<title>Sources with Alex Heath</title><description>Tech interviews.</description><language>en</language>
<itunes:author>Alex Heath</itunes:author>
<item><title>Ep 1</title><guid>g1</guid><enclosure url="https://cdn/1.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""


class _StubResolver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.urls = []

    def resolve_show(self, url):
        self.urls.append(url)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.fixture
def source():
    return RSSMediaSource()


def test_spotify_urls_are_valid_for_the_rss_source(source):
    assert source.is_valid_url(_SHOW_URL)
    assert source.is_valid_url("spotify:show:2aB3cD4eF5gH6iJ7kL8mN9")


def test_extract_rss_from_spotify_url_uses_injected_resolver(source):
    stub = _StubResolver(AppleShowMatch(collection_id="1", name="Sources", feed_url=_FEED_URL, score=0.98))
    source.spotify_resolver = stub

    assert source._extract_rss_from_spotify_url(_SHOW_URL) == _FEED_URL  # pylint: disable=protected-access
    assert stub.urls == [_SHOW_URL]


def test_non_spotify_url_is_left_alone(source):
    source.spotify_resolver = _StubResolver(AssertionError("must not be called"))
    assert (
        source._extract_rss_from_spotify_url("https://example.com/feed.xml") is None
    )  # pylint: disable=protected-access


def test_unresolvable_show_returns_none_without_raising(source):
    source.spotify_resolver = _StubResolver(SpotifyResolutionError("Could not find “X”."))
    assert source._extract_rss_from_spotify_url(_SHOW_URL) is None  # pylint: disable=protected-access


def test_extract_metadata_resolves_spotify_show_to_feed(source, monkeypatch):
    """The resolved feed URL — not the Spotify URL — becomes the podcast's rss_url."""
    source.spotify_resolver = _StubResolver(AppleShowMatch(collection_id="1", name="Sources", feed_url=_FEED_URL))
    fetched = []

    def fake_fetch(url, **kwargs):
        fetched.append(url)
        return FetchRSSResult(
            content=_FEED_XML, status_code=200, etag=None, last_modified=None, not_modified=False, error=None
        )

    monkeypatch.setattr(source, "fetch_rss_content", fake_fetch)

    metadata = source.extract_metadata(_SHOW_URL)

    assert fetched == [_FEED_URL]
    assert metadata is not None
    assert metadata["rss_url"] == _FEED_URL
    assert metadata["title"] == "Sources with Alex Heath"
