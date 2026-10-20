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
    core_title,
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
                {"id": "eeeeeeeeeee", "title": "AD FREE: Ep 1", "availability": "subscriber_only"},
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


class TestRetitledUploads:
    """Uploads retitled for YouTube: shared content words plus date and length."""

    def test_shared_words_with_duration_agreement(self):
        feed = _cand(
            title="20VC: Atlassian CEO on Why Agents Won't Replace Interfaces | Why Enterprises", duration=3329
        )
        video = _video(title="Atlassian CEO: Why Agents Won't Replace Interfaces | Why Enterprises", duration=3535)
        assert [x.match_method for x in match_candidates([feed], [video])] == ["title_duration"]
        feed = _cand(title="Building the Cloud for an Agentic World | AWS CEO Matt Garman", duration=3377)
        video = _video(title="Building the Cloud for AI Agents | AWS CEO Matt Garman", duration=3378)
        assert match_candidates([feed], [video])

    def test_shared_words_without_duration_agreement_do_not_match(self):
        feed = _cand(title="Building the Cloud for an Agentic World | AWS CEO Matt Garman", duration=3377)
        clip = _video(title="Building the Cloud for AI Agents | AWS CEO Matt Garman", duration=600)
        assert match_candidates([feed], [clip]) == []
        assert match_candidates([_cand(title=feed.title, duration=None)], [_video(title=clip.title)]) == []

    def test_too_few_shared_words_do_not_match(self):
        feed = _cand(title="When AI Agents Start Spending Your Money | Alex Rampell", duration=3089)
        video = _video(title="Agentic Commerce: Why Your Wallet Is About to Change", duration=3031)
        assert match_candidates([feed], [video]) == []

    def test_near_identical_length_needs_two_shared_words(self):
        feed = _cand(title="Rick Rubin: What Jay-Z Did in 30 Minutes Left Me Speechless!", duration=9985)
        video = _video(title="Rick Rubin: The Only Skill That Matters Now Is The One Thing AI Can't Do", duration=9990)
        assert [x.match_method for x in match_candidates([feed], [video])] == ["title_duration"]
        # Length and date alone picked another episode's upload in the A/B run.
        feed = _cand(title="20VC: Instinct Raises $1B at $10B Valuation | AMD Buys a World Model Lab", duration=4721)
        video = _video(title="Daniel Dines on Why AI Can't Replace Humans in Enterprise", duration=4744)
        assert match_candidates([feed], [video]) == []

    def test_near_identical_length_only_within_three_days(self):
        feed = _cand(title="Rick Rubin: What Jay-Z Did in 30 Minutes", pub_date=T0 - timedelta(days=4), duration=9985)
        video = _video(title="Rick Rubin: The Only Skill That Matters Now", duration=9990)
        assert match_candidates([feed], [video], now=T0 + timedelta(days=30)) == []

    def test_equal_titles_are_reserved_before_shared_words(self):
        full = _video("aaaaaaaaaaa", "Rich Roll on his crash course in entrepreneurship", duration=2136)
        exact = _cand("exact", title="Rich Roll on his crash course in entrepreneurship", duration=2135)
        retitled = _cand("retitled", title="Rich Roll's 'crash course' in entrepreneurship", duration=2135)
        m = match_candidates([retitled, exact], [full])
        assert [(x.episode_id, x.match_method) for x in m] == [("exact", "title_date")]

    def test_single_item_form_accepts_a_retitled_upload(self):
        c = _cand(title="Building a marketplace from scratch, with Hipcamp founder Alyssa Ravasio", duration=1922)
        title = "How Hipcamp's Alyssa Ravasio found a business model worth building"
        assert match_rule(c, title=title, released=T0, duration=1923, now=NOW) == "title_duration"


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


class TestCoreTitle:
    """Spec #87 Phase 3 — each side's own decoration is not part of the title."""

    def test_strips_episode_tag_and_show_name(self):
        feed = "#502 – Psychiatry, Insane Asylums, Mental Illness, ECT, Lobotomies, Freud & Jung"
        upload = "Psychiatry, Insane Asylums, Mental Illness, ECT, Lobotomies, Freud & Jung | Lex Fridman Podcast #502"
        assert core_title(feed, "Lex Fridman Podcast") == core_title(upload, "Lex Fridman Podcast")
        assert core_title("Ep 12: The Big One", "Show") == "the big one"
        assert core_title("Episode 12. The Big One | Show", "Show") == "the big one"
        assert core_title("Joe Rogan Experience #2519 - Scott Eastwood", "The Joe Rogan Experience") == "scott eastwood"

    def test_show_name_only_gives_an_empty_core_that_never_matches(self):
        assert core_title("Lex Fridman Podcast", "Lex Fridman Podcast") == ""
        c = _cand(title="Lex Fridman Podcast", duration=None)
        assert (
            _match(
                [c], [_video(title="Lex Fridman Podcast #1", duration=None)], now=NOW, show_name="Lex Fridman Podcast"
            )
            == []
        )
        # No show name given: cores are just tag-stripped titles.
        assert core_title("#5 Hello", None) == "hello"

    def test_lex_case_links_without_a_feed_duration(self):
        feed = _cand(
            "lex",
            title="#502 – Psychiatry, Insane Asylums, Mental Illness, ECT, Lobotomies, Freud & Jung",
            duration=None,
        )
        upload = _video(
            "aaaaaaaaaaa",
            "Psychiatry, Insane Asylums, Mental Illness, ECT, Lobotomies, Freud & Jung | Lex Fridman Podcast #502",
            duration=13016,
        )
        assert _match([feed], [upload], now=NOW) == []
        with_show = _match([feed], [upload], now=NOW, show_name="Lex Fridman Podcast")
        assert [(m.episode_id, m.match_method) for m in with_show] == [("lex", "title_date")]
        # The same through the single-item form used for claims and the Spotify probe.
        assert (
            match_rule(feed, title=upload.title, released=T0, duration=13016, now=NOW, show_name="Lex Fridman Podcast")
            == "title_date"
        )

    def test_core_equality_still_refuses_a_clearly_different_length(self):
        feed = _cand("lex", title="#502 – Psychiatry", duration=13000)
        clip = _video("aaaaaaaaaaa", "Psychiatry | Lex Fridman Podcast #502", duration=400)
        assert _match([feed], [clip], now=NOW, show_name="Lex Fridman Podcast") == []
