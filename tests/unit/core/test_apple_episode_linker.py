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

"""Spec #87 — pure matching of feed episodes to the iTunes lookup window."""

from datetime import datetime, timedelta, timezone

from thestill.core.apple_episode_linker import (
    AppleEpisodeEntry,
    apple_collection_id,
    match_candidates,
    parse_lookup_window,
)
from thestill.models.podcast import PlatformLinkCandidate

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def _entry(track_id="1", guid=None, audio=None, title=None, released=T0, url=None):
    return AppleEpisodeEntry(
        track_id=track_id,
        track_view_url=url or f"https://podcasts.apple.com/us/podcast/show/id100?i={track_id}",
        guid=guid,
        audio_url=audio,
        title=title,
        release_date=released,
    )


def _cand(episode_id="ep-1", external_id="", audio_url="", title="", pub_date=T0):
    return PlatformLinkCandidate(
        episode_id=episode_id, external_id=external_id, audio_url=audio_url, title=title, pub_date=pub_date
    )


class TestParseLookupWindow:
    def test_keeps_only_episode_entries_with_a_page(self):
        results = [
            {"wrapperType": "track", "kind": "podcast", "collectionId": 100},
            {
                "wrapperType": "podcastEpisode",
                "trackId": 42,
                "trackViewUrl": "https://podcasts.apple.com/us/podcast/x/id100?i=42",
                "episodeGuid": " guid-42 ",
                "episodeUrl": "https://cdn.example.com/42.mp3",
                "trackName": "Ep 42",
                "releaseDate": "2026-09-25T10:00:00Z",
            },
            {"wrapperType": "podcastEpisode", "trackId": 43},  # no page → dropped
            {"wrapperType": "podcastEpisode", "trackId": 44, "trackViewUrl": "http://insecure"},  # not https
            "garbage",
        ]
        entries = parse_lookup_window(results)
        assert [e.track_id for e in entries] == ["42"]
        assert entries[0].guid == "guid-42"
        assert entries[0].release_date == T0

    def test_bad_release_date_is_none(self):
        entries = parse_lookup_window(
            [{"wrapperType": "podcastEpisode", "trackId": 1, "trackViewUrl": "https://p", "releaseDate": "soon"}]
        )
        assert entries[0].release_date is None


class TestCollectionId:
    def test_extracts_from_show_url(self):
        assert apple_collection_id("https://podcasts.apple.com/us/podcast/prof-g/id1498802610") == "1498802610"

    def test_none_for_missing(self):
        assert apple_collection_id(None) is None
        assert apple_collection_id("https://example.com/no-id") is None


class TestMatchCandidates:
    def test_guid_wins(self):
        entries = [_entry("1", guid="g-1", audio="https://a/1.mp3", title="One")]
        matches = match_candidates([_cand(external_id="g-1", audio_url="https://a/other.mp3", title="Other")], entries)
        assert [(m.episode_id, m.match_method, m.external_ref) for m in matches] == [("ep-1", "guid", "1")]
        assert matches[0].url.endswith("?i=1")

    def test_audio_url_exact_then_loose(self):
        entries = [
            _entry("1", audio="https://a/1.mp3?tracking=x"),
            _entry("2", audio="http://cdn.example.com/2.mp3"),
        ]
        exact = match_candidates([_cand("e1", audio_url="https://a/1.mp3?tracking=x")], entries)
        loose = match_candidates([_cand("e2", audio_url="https://CDN.example.com/2.mp3?utm=1")], entries)
        assert (exact[0].external_ref, exact[0].match_method) == ("1", "audio_url")
        assert (loose[0].external_ref, loose[0].match_method) == ("2", "audio_url")

    def test_title_and_date_within_tolerance(self):
        entries = [_entry("1", title="The Big Episode!", released=T0)]
        ok = match_candidates([_cand(title="the big episode", pub_date=T0 + timedelta(hours=30))], entries)
        late = match_candidates([_cand(title="the big episode", pub_date=T0 + timedelta(hours=40))], entries)
        assert ok[0].match_method == "title_date"
        assert late == []

    def test_title_match_needs_a_pub_date_and_a_release_date(self):
        assert match_candidates([_cand(title="x", pub_date=None)], [_entry("1", title="x")]) == []
        assert match_candidates([_cand(title="x")], [_entry("1", title="x", released=None)]) == []

    def test_ambiguous_title_is_skipped(self):
        entries = [_entry("1", title="Weekly Recap"), _entry("2", title="Weekly Recap")]
        assert match_candidates([_cand(title="Weekly Recap")], entries) == []

    def test_each_entry_links_at_most_one_episode(self):
        entries = [_entry("1", guid="g", title="Same")]
        matches = match_candidates(
            [_cand("first", external_id="g"), _cand("second", external_id="g"), _cand("third", title="Same")],
            entries,
        )
        assert [m.episode_id for m in matches] == ["first"]

    def test_unmatched_candidates_are_absent(self):
        assert match_candidates([_cand(external_id="nope", title="nope")], [_entry("1", guid="g")]) == []

    def test_exact_matches_are_reserved_before_fuzzy_ones(self):
        """Two daily episodes titled alike; Apple has indexed only yesterday's.
        Newest-first, today's candidate must not take yesterday's entry by
        title while yesterday's own GUID match is still pending."""
        yesterday_entry = _entry("1", guid="g-yesterday", title="Daily Update", released=T0 - timedelta(days=1))
        today = _cand("today", external_id="g-today", title="Daily Update", pub_date=T0)
        yesterday = _cand("yesterday", external_id="g-yesterday", title="Daily Update", pub_date=T0 - timedelta(days=1))
        matches = match_candidates([today, yesterday], [yesterday_entry])
        assert [(m.episode_id, m.match_method) for m in matches] == [("yesterday", "guid")]
