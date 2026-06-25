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
returned ``had_error=False`` — a silent success that cleared
``last_refresh_error`` and never retried. It must now flag ``had_error=True``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from thestill.core.feed_manager import PodcastFeedManager
from thestill.core.media_source import FetchAndParseResult, RSSMediaSource
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
    )


def test_fetch_error_sentinel_sets_had_error(tmp_path, monkeypatch):
    fm = _feed_manager(tmp_path)
    source = RSSMediaSource(path_manager=PathManager(str(tmp_path)))
    monkeypatch.setattr(source, "fetch_and_parse", lambda *a, **k: _error_result())
    monkeypatch.setattr(fm.media_source_factory, "detect_source", lambda url: source)

    podcast, new_eps, had_error, hit, _src, _rot, _imgs = fm._refresh_single_podcast(_podcast(), None, set())

    assert had_error is True
    assert new_eps == []
    assert hit is False


def test_successful_fetch_is_not_flagged(tmp_path, monkeypatch):
    # A 304 (not_modified) is NOT an error — must stay had_error=False.
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

    _podcast_out, _eps, had_error, hit, _src, _rot, _imgs = fm._refresh_single_podcast(_podcast(), None, set())

    assert had_error is False
    assert hit is True  # conditional-GET hit, not an error
