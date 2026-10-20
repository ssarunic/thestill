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

"""Spec #87 Phase 3d — channel search parsing and the acceptance rule."""

from thestill.core.youtube_channel_discovery import (
    ChannelHit,
    ChannelScore,
    is_no_uploads_error,
    parse_search,
    pick_channel,
    same_channel,
    search_queries,
)


def _hit(name):
    return ChannelHit(url=f"https://www.youtube.com/@{name}", title=name, aliases=(f"https://www.youtube.com/@{name}",))


def _score(name, matches):
    return ChannelScore(_hit(name), matches)


class TestParseSearch:
    def test_prefers_the_handle_and_keeps_both_aliases(self):
        hits = parse_search(
            [
                {
                    "url": "https://www.youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg",
                    "channel_url": "https://www.youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg",
                    "uploader_url": "https://www.youtube.com/@hubermanlab",
                    "title": "Andrew Huberman",
                },
                {"url": "https://www.youtube.com/channel/UCxBcwypKK-W3GHd_RZ9FZrQ", "title": "Latent Space"},
            ]
        )
        assert [(h.url, h.title) for h in hits] == [
            ("https://www.youtube.com/@hubermanlab", "Andrew Huberman"),
            ("https://www.youtube.com/channel/UCxBcwypKK-W3GHd_RZ9FZrQ", "Latent Space"),
        ]
        assert hits[0].aliases == (
            "https://www.youtube.com/@hubermanlab",
            "https://www.youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg",
        )

    def test_drops_videos_generic_channels_and_junk(self):
        hits = parse_search(
            [
                {"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "A video"},
                {
                    "url": "https://www.youtube.com/channel/UCspotify000000000000",
                    "uploader_url": "https://www.youtube.com/@Spotify",
                    "title": "Spotify",
                },
                {"url": "https://evil.example/channel/UCx", "title": "Elsewhere"},
                "junk",
                {"title": "No url"},
            ]
        )
        assert hits == []


class TestSameChannel:
    def test_matches_either_alias_case_insensitively_and_ignores_tabs(self):
        hit = parse_search(
            [
                {
                    "channel_url": "https://www.youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg",
                    "uploader_url": "https://www.youtube.com/@hubermanlab",
                    "title": "Andrew Huberman",
                }
            ]
        )[0]
        assert same_channel(hit, "https://www.youtube.com/@HubermanLab/videos")
        assert same_channel(hit, "https://youtube.com/channel/UC2D2CMWXMOVWx7giW1n3LIg")
        assert not same_channel(hit, "https://www.youtube.com/@NYTPodcasts")
        assert not same_channel(hit, None)


class TestSearchQueries:
    def test_author_is_a_second_query_only_when_it_adds_something(self):
        assert search_queries("Possible", "Reid Hoffman") == ["Possible", "Possible Reid Hoffman"]
        assert search_queries("Deep Questions with Cal Newport", "Cal Newport") == ["Deep Questions with Cal Newport"]
        assert search_queries("Huberman Lab", None) == ["Huberman Lab"]
        assert search_queries("", "Someone") == []


class TestPickChannel:
    def test_accepts_a_clear_leader_with_three_matches(self):
        pick = pick_channel([_score("a16z", 4), _score("a16zcrypto", 1), _score("clips", 0)])
        assert (pick.reason, pick.channel.url) == ("accepted", "https://www.youtube.com/@a16z")
        assert pick_channel([_score("solo", 3)]).reason == "accepted"

    def test_fewer_than_three_matches_is_never_enough(self):
        # The Econoclasts: its own channel 0, UnHerd 2 and Glenn Diesen 1 through guest cross-posts.
        pick = pick_channel([_score("econoclasts", 0), _score("unherd", 2), _score("diesen", 1)])
        assert (pick.reason, pick.channel) == ("too_few", None)

    def test_a_close_runner_up_is_ambiguous(self):
        assert pick_channel([_score("a", 6), _score("b", 3)]).reason == "ambiguous"
        assert pick_channel([_score("a", 4), _score("b", 4)]).reason == "ambiguous"
        assert pick_channel([_score("a", 6), _score("b", 2)]).reason == "accepted"  # exactly a third

    def test_an_unverified_result_defers_even_beside_a_clear_leader(self):
        # The real channel's listing failed; a cross-poster with 3 must not win by default.
        pick = pick_channel([_score("crossposter", 3), _score("real", None)])
        assert (pick.reason, pick.channel) == ("deferred", None)

    def test_no_results(self):
        assert pick_channel([]).reason == "no_results"


def test_no_videos_tab_is_a_verified_empty_channel():
    assert is_no_uploads_error(Exception("ERROR: [youtube:tab] UC8Z: This channel does not have a videos tab"))
    assert not is_no_uploads_error(Exception("Sign in to confirm you're not a bot"))
    assert not is_no_uploads_error(Exception("YouTube said: This channel is not available."))
