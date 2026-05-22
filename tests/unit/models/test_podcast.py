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

"""Model-boundary tz-awareness tests (spec #42, FM-3).

A naive ``last_processed`` / ``pub_date`` must be impossible to *hold*, not
just impossible to compare — the model coerces to tz-aware UTC at
construction so no downstream comparison can mix awareness.
"""

from datetime import datetime, timezone

from thestill.models.podcast import Episode, Podcast


class TestPodcastLastProcessedAware:
    def test_naive_last_processed_coerced_to_utc(self):
        podcast = Podcast(
            title="Test",
            description="d",
            rss_url="https://example.com/feed.xml",
            last_processed=datetime(2026, 5, 18, 7, 7, 0),  # naive
        )
        assert podcast.last_processed.tzinfo == timezone.utc

    def test_aware_last_processed_preserved(self):
        aware = datetime(2026, 5, 18, 7, 7, 0, tzinfo=timezone.utc)
        podcast = Podcast(
            title="Test",
            description="d",
            rss_url="https://example.com/feed.xml",
            last_processed=aware,
        )
        assert podcast.last_processed == aware

    def test_none_last_processed_stays_none(self):
        podcast = Podcast(title="Test", description="d", rss_url="https://example.com/feed.xml")
        assert podcast.last_processed is None

    def test_coerced_last_processed_compares_to_aware_pub_date(self):
        """The exact comparison that crashed mid-refresh must now be safe."""
        podcast = Podcast(
            title="Test",
            description="d",
            rss_url="https://example.com/feed.xml",
            last_processed=datetime(2026, 5, 18, 7, 7, 0),  # naive in, aware out
        )
        newer = datetime(2026, 5, 21, 7, 7, 0, tzinfo=timezone.utc)
        # Would raise TypeError if last_processed were still naive.
        assert newer > podcast.last_processed


class TestEpisodePubDateAware:
    def test_naive_pub_date_coerced_to_utc(self):
        episode = Episode(
            title="Ep",
            description="d",
            external_id="ep-1",
            audio_url="https://example.com/ep.mp3",
            pub_date=datetime(2026, 5, 21, 7, 7, 0),  # naive
        )
        assert episode.pub_date.tzinfo == timezone.utc
