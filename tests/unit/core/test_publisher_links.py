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

"""Spec #87 — links the publisher already put in the feed."""

from thestill.core.publisher_links import publisher_links_for_candidate, show_links_from_sources
from thestill.models.podcast import AlternateEnclosure, PlatformLinkCandidate

SPOT = "0tkEdaVIsQNGKUGUKTPqeH"
SPOT2 = "1AAAAAAAAAAAAAAAAAAAAA"


def _cand(**kw):
    base = dict(episode_id="e", external_id="g", audio_url="https://a/x.mp3", title="T")
    base.update(kw)
    return PlatformLinkCandidate(**base)


class TestEpisodeLinks:
    def test_import_canonical_ids(self):
        s = publisher_links_for_candidate(_cand(canonical_id=f"spotify:{SPOT}"))["spotify"]
        y = publisher_links_for_candidate(_cand(canonical_id="youtube:aaaaaaaaaaa"))["youtube"]
        assert (s.url, s.external_ref, s.source) == (f"https://open.spotify.com/episode/{SPOT}", SPOT, "import")
        assert s.verified and y.verified
        assert (y.url, y.source) == ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "import")

    def test_alternate_enclosure_beats_description(self):
        alts = [
            AlternateEnclosure(episode_id="e", source_uri="https://youtu.be/bbbbbbbbbbb", mime_type="video/youtube"),
            AlternateEnclosure(
                episode_id="e", source_uri="https://youtu.be/ccccccccccc", mime_type="video/youtube", is_default=True
            ),
            AlternateEnclosure(episode_id="e", source_uri="https://x/y.mp4", mime_type="video/mp4"),
        ]
        link = publisher_links_for_candidate(_cand(description_html="youtu.be/ddddddddddd"), alts)["youtube"]
        assert (link.external_ref, link.source) == ("ccccccccccc", "alternate_enclosure")

    def test_item_link(self):
        links = publisher_links_for_candidate(_cand(website_url=f"https://open.spotify.com/episode/{SPOT}?si=x"))
        assert links["spotify"].source == "item_link"
        links = publisher_links_for_candidate(_cand(website_url="https://www.youtube.com/watch?v=aaaaaaaaaaa"))
        assert links["youtube"].source == "item_link"

    def test_description_link_only_when_unique(self):
        one = publisher_links_for_candidate(
            _cand(description_html=f'<a href="https://open.spotify.com/episode/{SPOT}">x</a>')
        )
        assert one["spotify"].external_ref == SPOT and one["spotify"].source == "description"
        assert one["spotify"].verified is False  # a claim to verify, not a link yet
        two = publisher_links_for_candidate(
            _cand(description_html=f"open.spotify.com/episode/{SPOT} open.spotify.com/episode/{SPOT2}")
        )
        assert "spotify" not in two
        repeated = publisher_links_for_candidate(
            _cand(description_html=f"open.spotify.com/episode/{SPOT} open.spotify.com/episode/{SPOT}")
        )
        assert repeated["spotify"].external_ref == SPOT
        assert "youtube" not in publisher_links_for_candidate(
            _cand(description_html="youtu.be/aaaaaaaaaaa youtu.be/bbbbbbbbbbb")
        )

    def test_show_links_are_not_episode_links(self):
        assert (
            publisher_links_for_candidate(_cand(description_html=f"open.spotify.com/show/{SPOT} youtube.com/@handle"))
            == {}
        )


class TestShowLinks:
    def test_podcast_level_unique_link_wins(self):
        show = show_links_from_sources(
            [f"Listen on https://open.spotify.com/show/{SPOT}", "https://www.youtube.com/@profg"],
            [f"open.spotify.com/show/{SPOT2}"] * 5,
        )
        assert show.spotify_url == f"https://open.spotify.com/show/{SPOT}"
        assert show.youtube_url == "https://www.youtube.com/@profg"

    def test_podcast_level_ambiguity_falls_through_to_votes(self):
        show = show_links_from_sources(
            [f"open.spotify.com/show/{SPOT} open.spotify.com/show/{SPOT2}"],
            [f"open.spotify.com/show/{SPOT2}"] * 3,
        )
        assert show.spotify_url == f"https://open.spotify.com/show/{SPOT2}"

    def test_votes_need_three_and_no_competitor(self):
        assert show_links_from_sources([], ["youtube.com/@a"] * 2).youtube_url is None
        assert show_links_from_sources([], ["youtube.com/@a"] * 3).youtube_url == "https://www.youtube.com/@a"
        assert show_links_from_sources([], ["youtube.com/@a"] * 3 + ["youtube.com/@guest"]).youtube_url is None

    def test_nothing(self):
        assert show_links_from_sources([None, ""], [None]) == show_links_from_sources([], [])
