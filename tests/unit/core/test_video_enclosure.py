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

"""Spec #61 §6 — RSS video enclosures are ingested (audio still wins)."""

from thestill.core.media_source import RSSMediaSource


def _entry(links=None, enclosures=None) -> dict:
    return {"links": links or [], "enclosures": enclosures or []}


class TestExtractEnclosureInfo:
    def setup_method(self):
        self.source = RSSMediaSource()

    def test_audio_enclosure_extracted(self):
        entry = _entry(enclosures=[{"type": "audio/mpeg", "href": "https://x.test/ep.mp3", "length": "123"}])
        url, size, mime = self.source._extract_enclosure_info(entry)
        assert url == "https://x.test/ep.mp3"
        assert size == 123
        assert mime == "audio/mpeg"

    def test_video_enclosure_accepted_when_no_audio(self):
        entry = _entry(enclosures=[{"type": "video/mp4", "href": "https://x.test/ep.mp4", "length": "999"}])
        url, size, mime = self.source._extract_enclosure_info(entry)
        assert url == "https://x.test/ep.mp4"
        assert size == 999
        assert mime == "video/mp4"

    def test_audio_preferred_over_video(self):
        # Pipeline is audio-first: when a (nonstandard) feed carries both,
        # the audio enclosure stays the episode's primary media.
        entry = _entry(
            links=[{"type": "video/mp4", "href": "https://x.test/ep.mp4"}],
            enclosures=[{"type": "audio/mpeg", "href": "https://x.test/ep.mp3"}],
        )
        url, _, mime = self.source._extract_enclosure_info(entry)
        assert url == "https://x.test/ep.mp3"
        assert mime == "audio/mpeg"

    def test_video_in_links_accepted(self):
        entry = _entry(links=[{"type": "video/mp4", "href": "https://x.test/ep.mp4"}])
        url, size, mime = self.source._extract_enclosure_info(entry)
        assert url == "https://x.test/ep.mp4"
        assert size is None
        assert mime == "video/mp4"

    def test_non_media_entry_returns_none(self):
        entry = _entry(links=[{"type": "text/html", "href": "https://x.test/page"}])
        assert self.source._extract_enclosure_info(entry) == (None, None, None)

    def test_enclosure_without_href_skipped(self):
        entry = _entry(enclosures=[{"type": "video/mp4"}])
        assert self.source._extract_enclosure_info(entry) == (None, None, None)
