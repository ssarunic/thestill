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

"""Regression tests for spec #25 item 3.4 — feed URL scheme validation."""

from unittest.mock import MagicMock

import pytest

from thestill.core.feed_manager import PodcastFeedManager


@pytest.fixture
def feed_manager(tmp_path):
    repo = MagicMock()
    repo.exists.return_value = False
    path_manager = MagicMock()
    path_manager.storage_path = tmp_path
    path_manager.original_audio_dir.return_value = tmp_path / "audio"
    fm = PodcastFeedManager(repo, path_manager)
    # Block the downstream media-source factory so a failing scheme check
    # can be distinguished from a failing metadata fetch.
    fm.media_source_factory = MagicMock()
    fm.media_source_factory.detect_source.side_effect = AssertionError(
        "should have rejected at scheme check"
    )
    return fm


class TestAddPodcastSchemeCheck:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/feed.xml",
            "gopher://example.com/",
            "javascript:alert(1)",
            "data:text/xml,<rss></rss>",
            "",
        ],
    )
    def test_non_http_schemes_refused(self, feed_manager, url):
        result = feed_manager.add_podcast(url)
        assert result is None

    def test_http_and_https_reach_media_source(self, feed_manager):
        """http/https pass the scheme check and hit the (mocked) factory,
        which is rigged to assert if called — so the test passes iff the
        factory is reached exactly once."""
        feed_manager.media_source_factory = MagicMock()
        feed_manager.media_source_factory.detect_source.return_value.extract_metadata.return_value = None
        # extract_metadata returning None drops us into the "could not
        # extract" branch, but crucially the scheme check must pass.
        feed_manager.add_podcast("https://example.com/feed.xml")
        feed_manager.media_source_factory.detect_source.assert_called_once()
