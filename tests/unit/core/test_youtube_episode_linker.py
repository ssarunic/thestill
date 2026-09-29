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

"""Spec #87 Phase 2 — matching feed episodes to a channel's uploads."""

from datetime import datetime, timedelta, timezone

from thestill.core.youtube_episode_linker import (
    YouTubeVideoEntry,
    channel_videos_url,
    claim_corroborated,
    date_tolerance,
    episode_number,
)
from thestill.core.youtube_episode_linker import match_candidates as _match
from thestill.core.youtube_episode_linker import match_rule, parse_listing
from thestill.models.podcast import PlatformLinkCandidate

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=2)  # inside the "recent" band: 3-day date tolerance


def match_candidates(candidates, entries, *, now=NOW):
    return _match(candidates, entries, now=now)


def _video(video_id="aaaaaaaaaaa", title="The Big Episode", uploaded=T0, duration=3600):
    return YouTubeVideoEntry(video_id=video_id, title=title, uploaded=uploaded, duration=duration)


def _cand(episode_id="ep", title="The Big Episode", pub_date=T0, duration=3600):
    return PlatformLinkCandidate(
        episode_id=episode_id,
        external_id="g",
        audio_url="https://a/x.mp3",
        title=title,
        pub_date=pub_date,
        duration=duration,
    )


class TestChannelUrl:
    def test_appends_videos_tab(self):
        assert channel_videos_url("https://www.youtube.com/@lexfridman") == "https://www.youtube.com/@lexfridman/videos"
        assert (
            channel_videos_url("https://www.youtube.com/channel/UCx/") == "https://www.youtube.com/channel/UCx/videos"
        )

    def test_keeps_explicit_tabs_and_playlists(self):
        assert channel_videos_url("https://www.youtube.com/@x/videos") == "https://www.youtube.com/@x/videos"
        assert (
            channel_videos_url("https://www.youtube.com/playlist?list=PL1")
            == "https://www.youtube.com/playlist?list=PL1"
        )


class TestParseListing:
    def test_keeps_well_formed_uploads_only(self):
        entries = parse_listing(
            [
                {"id": "aaaaaaaaaaa", "title": " Ep 1 ", "timestamp": 1789689600, "duration": 100.0},
                {"id": "bbbbbbbbbbb", "title": "Old", "upload_date": "20260101"},
                {"id": "ccccccccccc", "title": "Live", "live_status": "is_live"},
                {"id": "short", "title": "bad id"},
                {"id": "ddddddddddd", "title": ""},
                "junk",
            ]
        )
        assert [(e.video_id, e.title, e.duration) for e in entries] == [
            ("aaaaaaaaaaa", "Ep 1", 100),
            ("bbbbbbbbbbb", "Old", None),
        ]
        assert entries[0].uploaded == datetime.fromtimestamp(1789689600, tz=timezone.utc)
        assert entries[1].uploaded == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestMatch:
    def test_exact_title_within_date_and_duration(self):
        m = match_candidates([_cand()], [_video(duration=3700)])
        assert [(x.episode_id, x.external_ref, x.match_method) for x in m] == [("ep", "aaaaaaaaaaa", "title_date")]
        assert m[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"

    def test_exact_title_without_durations_still_matches(self):
        assert match_candidates([_cand(duration=None)], [_video(duration=None)])[0].match_method == "title_date"

    def test_exact_title_with_clearly_different_length_is_a_clip(self):
        assert match_candidates([_cand(duration=3600)], [_video(duration=400)]) == []

    def test_date_tolerance_recent(self):
        assert match_candidates([_cand(pub_date=T0 - timedelta(days=3))], [_video()])
        assert match_candidates([_cand(pub_date=T0 - timedelta(days=4))], [_video()]) == []

    def test_date_tolerance_widens_with_age(self):
        # "N weeks ago" band: the listing's date may be up to two weeks off.
        old = T0 - timedelta(days=40)
        assert match_candidates([_cand(pub_date=old)], [_video(uploaded=old + timedelta(days=10))], now=T0)
        assert match_candidates([_cand(pub_date=old)], [_video(uploaded=old + timedelta(days=20))], now=T0) == []
        # "N months ago" band: 45 days.
        older = T0 - timedelta(days=300)
        assert match_candidates([_cand(pub_date=older)], [_video(uploaded=older + timedelta(days=40))], now=T0)
        assert date_tolerance(T0 - timedelta(days=1), T0) == timedelta(days=3)
        assert date_tolerance(T0 - timedelta(days=50), T0) == timedelta(days=14)
        assert date_tolerance(T0 - timedelta(days=100), T0) == timedelta(days=45)
        assert date_tolerance(T0 - timedelta(days=400), T0) == timedelta(days=45)

    def test_shared_episode_number_with_duration(self):
        feed = _cand(title="#502 – Guest Name: Topic of the Day", duration=13000)
        video = _video(title="Topic of the Day, Subtopics | Show Name #502", duration=13016)
        assert match_candidates([feed], [video])[0].match_method == "title_duration"
        # Number alone, without a duration on either side, is not enough.
        assert match_candidates([_cand(title="#502 – X", duration=None)], [_video(title="Y #502")]) == []
        # Different numbers never match by number.
        assert match_candidates([_cand(title="#503 – X", duration=13000)], [video]) == []
        assert episode_number("#0502 and #502") == "502"
        assert episode_number("#1 vs #2") is None
        assert episode_number("no number") is None

    def test_contained_title_needs_duration_agreement(self):
        long_title = "Ep 12: The Big Episode with a Guest | Show Name"
        ok = match_candidates([_cand(title="The Big Episode with a Guest")], [_video(title=long_title, duration=3500)])
        assert ok[0].match_method == "title_duration"
        assert (
            match_candidates([_cand(title="The Big Episode with a Guest", duration=None)], [_video(title=long_title)])
            == []
        )
        assert (
            match_candidates([_cand(title="Big Episode")], [_video(title=long_title, duration=3600)]) == []
        )  # too short

    def test_ambiguous_candidates_are_skipped(self):
        videos = [_video("aaaaaaaaaaa"), _video("bbbbbbbbbbb")]
        assert match_candidates([_cand()], videos) == []

    def test_each_upload_links_once(self):
        m = match_candidates([_cand("one"), _cand("two")], [_video()])
        assert [x.episode_id for x in m] == ["one"]

    def test_no_pub_date_or_upload_date_no_match(self):
        assert match_candidates([_cand(pub_date=None)], [_video()]) == []
        assert match_candidates([_cand()], [_video(uploaded=None)]) == []

    def test_equal_titles_are_reserved_before_contained_ones(self):
        full = _video("aaaaaaaaaaa", "The Big Episode with a Guest", duration=3600)
        exact = _cand("exact", title="The Big Episode with a Guest", duration=3600)
        prefixed = _cand("prefixed", title="Ep 12: The Big Episode with a Guest", duration=3600)
        # Newest-first order puts the prefixed candidate first; it must not take the exact one's upload.
        m = match_candidates([prefixed, exact], [full])
        assert [(x.episode_id, x.match_method) for x in m] == [("exact", "title_date")]


class TestMatchRule:
    def test_single_item_form_shares_the_rules(self):
        c = _cand(title="#502 – Guest: Topic", duration=13000)
        assert match_rule(c, title="Topic | Show #502", released=T0, duration=13016, now=NOW) == "title_duration"
        assert match_rule(c, title="#502 – Guest: Topic", released=T0, duration=13000, now=NOW) == "title_date"
        assert match_rule(c, title="Last week's episode", released=T0, duration=13000, now=NOW) is None
        assert match_rule(c, title="#502 – Guest: Topic", released=None, duration=13000, now=NOW) is None

    def test_claim_corroboration_needs_date_and_duration(self):
        c = _cand(title="Nick Lane – Life as we know it", duration=4808)
        assert claim_corroborated(c, released=T0, duration=4853, now=NOW)  # retitled upload, same day, same length
        assert not claim_corroborated(c, released=T0, duration=822, now=NOW)  # a bite-size cut
        assert not claim_corroborated(c, released=T0, duration=None, now=NOW)  # unknown length is no proof
        assert not claim_corroborated(c, released=T0 - timedelta(days=10), duration=4808, now=NOW)
        assert not claim_corroborated(_cand(duration=None), released=T0, duration=4808, now=NOW)
