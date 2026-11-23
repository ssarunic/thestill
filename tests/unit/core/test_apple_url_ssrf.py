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

"""A user-supplied Apple Podcasts URL is fetched only through the SSRF guard.

The fallback that resolves an Apple page (when the iTunes lookup by id finds
nothing) used ``urllib.request.urlopen``, which follows redirects without
re-validating each hop. It now goes through ``guarded_get``, whose session
validates every hop, and only for a real Apple host.
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from thestill.core import media_source
from thestill.core.media_source import RSSMediaSource
from thestill.utils.url_guard import UnsafeDestinationError

APPLE_URL = "https://podcasts.apple.com/us/podcast/some-show/id1234567890"


def _lookup_urlopen(results_by_id):
    """Fake ``urlopen`` for the fixed iTunes lookup host only."""
    calls = []

    def fake(target, *args, **kwargs):
        url = target if isinstance(target, str) else target.full_url
        calls.append(url)
        assert url.startswith("https://itunes.apple.com/lookup?id="), f"unguarded fetch of {url}"
        pid = url.rsplit("=", 1)[1]
        payload = results_by_id.get(pid, {"resultCount": 0, "results": []})
        return io.BytesIO(json.dumps(payload).encode())

    return fake, calls


class TestAppleRedirectFetch:
    def test_page_is_fetched_through_the_guard_not_urllib(self):
        source = RSSMediaSource()
        fake_urlopen, calls = _lookup_urlopen(
            {"9876543210": {"resultCount": 1, "results": [{"feedUrl": "https://feeds.example.com/show.xml"}]}}
        )
        page = SimpleNamespace(text="<html>id9876543210</html>", raise_for_status=lambda: None)
        with (
            patch.object(media_source.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(media_source, "guarded_get", return_value=page) as guarded,
        ):
            assert source._extract_rss_from_apple_url(APPLE_URL) == "https://feeds.example.com/show.xml"
        guarded.assert_called_once()
        assert guarded.call_args.args[0] == APPLE_URL
        assert "timeout" in guarded.call_args.kwargs

    def test_redirect_to_a_private_address_is_refused(self):
        source = RSSMediaSource()
        fake_urlopen, _ = _lookup_urlopen({})
        with (
            patch.object(media_source.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(media_source, "guarded_get", side_effect=UnsafeDestinationError("169.254.169.254")),
        ):
            assert source._extract_rss_from_apple_url(APPLE_URL) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/?next=podcasts.apple.com/id1234567890",
            "https://podcasts.apple.com.evil.example/us/podcast/x/id1234567890",
        ],
    )
    def test_non_apple_host_is_not_fetched(self, url):
        source = RSSMediaSource()
        with (
            patch.object(media_source.urllib.request, "urlopen", side_effect=AssertionError("no fetch")),
            patch.object(media_source, "guarded_get", side_effect=AssertionError("no fetch")),
        ):
            assert source._extract_rss_from_apple_url(url) is None
