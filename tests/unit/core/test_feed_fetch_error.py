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

"""Regression: feed fetch failures must NOT report refresh success.

``RSSMediaSource.fetch_and_parse`` returns an error SENTINEL (content /
parsed_feed None, ``error`` set) on DNS/HTTP failures rather than raising.
``_refresh_single_podcast`` previously fell through to ``episodes = []`` and
reported success — a silent success that cleared ``last_refresh_error`` and
never retried. It must now return a classified ``RefreshFailure`` (spec #60).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.media_source import FetchAndParseResult, RSSMediaSource
from thestill.core.refresh_failure import RefreshFailureKind
from thestill.models.podcast import Podcast
from thestill.utils.path_manager import PathManager


def _feed_manager(tmp_path: Path) -> PodcastFeedManager:
    pm = PathManager(str(tmp_path))

    class _Repo:  # _refresh_single_podcast never touches the repo
        pass

    return PodcastFeedManager(_Repo(), pm)


def _podcast() -> Podcast:
    return Podcast(
        id="00000000-0000-0000-0000-000000000001",
        rss_url="https://example.com/feed.xml",
        title="Fetch Error Test",
        description="",
        episodes=[],
        last_refresh_error=None,
        pub_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _error_result() -> FetchAndParseResult:
    return FetchAndParseResult(
        content=None,
        parsed_feed=None,
        status_code=0,
        etag=None,
        last_modified=None,
        not_modified=False,
        error="[Errno 8] nodename nor servname provided, or not known",
        kind=RefreshFailureKind.CONNECTIVITY,
    )


def test_fetch_error_sentinel_sets_failure(tmp_path, monkeypatch):
    fm = _feed_manager(tmp_path)
    source = RSSMediaSource(path_manager=PathManager(str(tmp_path)))
    monkeypatch.setattr(source, "fetch_and_parse", lambda *a, **k: _error_result())
    monkeypatch.setattr(fm.media_source_factory, "detect_source", lambda url: source)

    result = fm._refresh_single_podcast(_podcast(), None, set())

    assert result.had_error is True
    assert result.failure is not None
    assert result.failure.kind is RefreshFailureKind.CONNECTIVITY
    assert result.new_episodes == []
    assert result.conditional_hit is False


def test_fetch_error_sentinel_without_kind_defaults_to_connectivity(tmp_path, monkeypatch):
    # Defensive: a sentinel that somehow lost its kind must classify as
    # connectivity (the keep-trying bias), never anything park-eligible.
    fm = _feed_manager(tmp_path)
    source = RSSMediaSource(path_manager=PathManager(str(tmp_path)))
    bare = _error_result()._replace(kind=None)
    monkeypatch.setattr(source, "fetch_and_parse", lambda *a, **k: bare)
    monkeypatch.setattr(fm.media_source_factory, "detect_source", lambda url: source)

    result = fm._refresh_single_podcast(_podcast(), None, set())

    assert result.failure is not None
    assert result.failure.kind is RefreshFailureKind.CONNECTIVITY


def test_youtube_outage_classifies_connectivity_end_to_end(tmp_path, monkeypatch):
    """Spec #60 review regression: a yt-dlp network failure must propagate out
    of YouTubeMediaSource (previously swallowed to [] one layer down in
    youtube_downloader, faking a clean 'no new episodes' refresh) and classify
    as connectivity in _refresh_single_podcast's catch-all."""
    from unittest.mock import Mock

    from yt_dlp.utils import DownloadError

    from thestill.core.media_source import YouTubeMediaSource

    fm = _feed_manager(tmp_path)
    source = YouTubeMediaSource(str(tmp_path))
    source.youtube_downloader.get_episodes_from_playlist = Mock(
        side_effect=DownloadError("Unable to download webpage: <urlopen error [Errno 8] nodename nor servname>")
    )
    # detect_source is patched, so the podcast's stored URL is irrelevant —
    # the YouTube source path is what's under test.
    monkeypatch.setattr(fm.media_source_factory, "detect_source", lambda url: source)

    result = fm._refresh_single_podcast(_podcast(), None, set())

    assert result.failure is not None
    assert result.failure.kind is RefreshFailureKind.CONNECTIVITY
    assert result.new_episodes == []


def test_successful_fetch_is_not_flagged(tmp_path, monkeypatch):
    # A 304 (not_modified) is NOT an error — must stay failure=None.
    fm = _feed_manager(tmp_path)
    source = RSSMediaSource(path_manager=PathManager(str(tmp_path)))
    not_modified = FetchAndParseResult(
        content=None,
        parsed_feed=None,
        status_code=304,
        etag="W/abc",
        last_modified=None,
        not_modified=True,
        error=None,
    )
    monkeypatch.setattr(source, "fetch_and_parse", lambda *a, **k: not_modified)
    monkeypatch.setattr(fm.media_source_factory, "detect_source", lambda url: source)

    result = fm._refresh_single_podcast(_podcast(), None, set())

    assert result.had_error is False
    assert result.failure is None
    assert result.conditional_hit is True  # conditional-GET hit, not an error
