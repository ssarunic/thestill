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

"""Live-network canary for the Apple deep-history lookup (spec #65).

The Tier-3 page scrape depends on Apple's unversioned page internals, so
the committed HTML fixture can go stale without any unit test noticing.
This test hits the real page and is the drift canary — run it whenever
``apple_page_parse_failed`` shows up in production logs.

Gated: run with ``NETWORK_TESTS=1 ./venv/bin/pytest tests/integration/test_apple_deep_history_network.py``.
"""

import os

import pytest

from thestill.services.import_service import ApplePodcastsResolver

# The episode that motivated spec #65: deeper than the newest 200 the
# iTunes API returns for the show, so only Tier 3 can resolve it.
_DEEP_EPISODE_URL = (
    "https://podcasts.apple.com/gb/podcast/"
    "20vc-revolut-founder-nik-storonsky-on-when-and-where/id958230465?i=1000678898255"
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("NETWORK_TESTS") != "1",
        reason="live-network test; set NETWORK_TESTS=1 to run",
    ),
]


def test_deep_history_episode_resolves_end_to_end():
    src = ApplePodcastsResolver().resolve(_DEEP_EPISODE_URL)

    assert src.kind == "apple_episode"
    assert src.canonical_id == "apple:1000678898255"
    assert "Storonsky" in src.title
    assert src.audio_url.startswith("https://") and "libsyn" in src.audio_url
    assert src.parent is not None
    assert src.parent.rss_url.startswith("https://")
