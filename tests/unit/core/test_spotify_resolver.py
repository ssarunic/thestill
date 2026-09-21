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

"""Spotify link resolution (spec #79) — offline unit tests.

Covers the three stages independently (Spotify metadata parsing, Apple show
match, episode match) and the orchestration tiers end-to-end with every
network collaborator injected.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from thestill.core import spotify_resolver
from thestill.core.spotify_resolver import (
    EPISODE_ACCEPT_THRESHOLD,
    AppleShowMatch,
    EpisodeCandidate,
    SpotifyEpisodeMetadata,
    SpotifyLinkResolver,
    SpotifyResolutionError,
    SpotifyShowMetadata,
    candidates_from_feed,
    candidates_from_itunes,
    episode_metadata_from_api,
    episode_number,
    find_apple_show,
    normalise_title,
    parse_episode_page,
    parse_meta_tags,
    parse_show_page,
    pick_episode,
    score_episode_candidate,
    score_show_candidate,
    show_metadata_from_api,
    token_set_ratio,
)

_FIXTURES = Path(__file__).parents[2] / "fixtures" / "spotify"
_EPISODE_ID = "7kQ2xN9pZ1aB3cD4eF5gH6"
_SHOW_ID = "2aB3cD4eF5gH6iJ7kL8mN9"
_EPISODE_URL = f"https://open.spotify.com/episode/{_EPISODE_ID}?si=abc123"
_SHOW_URL = f"https://open.spotify.com/show/{_SHOW_ID}"
_RELEASE = datetime(2026, 9, 8, 22, 2, tzinfo=timezone.utc)


def _episode_html() -> str:
    return (_FIXTURES / "episode-page.html").read_text(encoding="utf-8")


def _show_html() -> str:
    return (_FIXTURES / "show-page.html").read_text(encoding="utf-8")


def _meta(**overrides) -> SpotifyEpisodeMetadata:
    base = dict(
        episode_id=_EPISODE_ID,
        title="Mark Zuckerberg on Muse, Meta's biggest AI bet yet",
        show_name="Sources with Alex Heath",
        publisher="Alex Heath",
        release_date=_RELEASE,
        duration_seconds=4210,
    )
    base.update(overrides)
    return SpotifyEpisodeMetadata(**base)


def _candidate(title, *, hours=0, duration=4210, audio="https://cdn.example.com/ep.mp3", **kw) -> EpisodeCandidate:
    return EpisodeCandidate(
        title=title,
        audio_url=audio,
        pub_date=_RELEASE + timedelta(hours=hours),
        duration_seconds=duration,
        **kw,
    )


# ---------------------------------------------------------------------------
# Stage 1 — Spotify metadata
# ---------------------------------------------------------------------------


class TestPageParsing:
    def test_meta_tags_first_occurrence_wins_and_unescapes(self):
        tags = parse_meta_tags(
            '<meta property="og:title" content="A &amp; B"><meta content="second" property="og:title">'
            "<meta name='description' content='plain'>"
        )
        assert tags == {"og:title": "A & B", "description": "plain"}

    def test_episode_page_fixture(self):
        meta = parse_episode_page(_episode_html(), _EPISODE_ID)
        assert meta.episode_id == _EPISODE_ID
        assert meta.title == "Mark Zuckerberg on Muse, Meta's biggest AI bet yet"
        assert meta.show_name == "Sources with Alex Heath"
        assert meta.publisher == "Alex Heath"
        assert meta.release_date == _RELEASE
        assert meta.duration_seconds == 4210
        assert meta.image_url.startswith("https://i.scdn.co/image/")
        assert meta.description.startswith("Listen to this episode from Sources with Alex Heath")
        assert meta.source == "page"

    def test_episode_page_show_from_og_description_when_title_tag_missing(self):
        html = (
            '<meta property="og:title" content="Ep 12: Hello">'
            '<meta property="og:description" content="My Show · Episode">'
        )
        meta = parse_episode_page(html, _EPISODE_ID)
        assert (meta.title, meta.show_name) == ("Ep 12: Hello", "My Show")
        assert meta.release_date is None and meta.duration_seconds is None

    def test_episode_page_legacy_listen_to_description(self):
        html = (
            "<title>Hello - Podcast on Spotify</title>"
            '<meta property="og:title" content="Hello">'
            '<meta property="og:description" content="Listen to this episode from My Show on Spotify. Notes here.">'
        )
        meta = parse_episode_page(html, _EPISODE_ID)
        assert meta.show_name == "My Show"
        assert meta.description == "Notes here."

    def test_episode_page_without_show_name_raises_user_facing_error(self):
        with pytest.raises(SpotifyResolutionError, match="did not expose the episode title and show name"):
            parse_episode_page('<meta property="og:title" content="Only a title">', _EPISODE_ID)

    def test_show_page_fixture(self):
        meta = parse_show_page(_show_html(), _SHOW_ID)
        assert meta.name == "Sources with Alex Heath"
        assert meta.publisher == "Alex Heath"
        assert meta.description.startswith("Listen to Sources with Alex Heath on Spotify.")
        assert meta.image_url.startswith("https://i.scdn.co/image/")

    def test_web_player_shell_is_not_mistaken_for_a_show(self):
        # What a browser UA gets: the JS shell, no Open Graph tags.
        shell = "<html><head><title>Spotify – Web Player</title></head></html>"
        with pytest.raises(SpotifyResolutionError, match="show name"):
            parse_show_page(shell, _SHOW_ID)
        with pytest.raises(SpotifyResolutionError, match="episode title and show name"):
            parse_episode_page(shell, _EPISODE_ID)

    def test_spotify_pages_are_fetched_with_a_non_browser_user_agent(self, monkeypatch):
        # Spotify serves the metadata page to non-browser agents only.
        seen = {}

        class _Resp:
            status_code, text, url = 200, "<html></html>", "https://open.spotify.com/x"

        class _Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, **kwargs):
                return _Resp()

        def fake_session(*, user_agent, retries):
            seen["ua"] = user_agent
            return _Session()

        monkeypatch.setattr(spotify_resolver, "guarded_session", fake_session)
        spotify_resolver.fetch_page("https://open.spotify.com/show/x")
        assert "Mozilla" not in seen["ua"]

    def test_show_page_without_name_raises(self):
        with pytest.raises(SpotifyResolutionError, match="show name"):
            parse_show_page("<html><head></head></html>", _SHOW_ID)

    def test_parser_is_bounded_on_huge_pages(self):
        # 5 MB of junk before the head must not blow up (regexes are bounded).
        html = "<div>" + ("x" * 5_000_000) + '</div><meta property="og:title" content="late">'
        assert parse_meta_tags(html) == {}


class TestApiMapping:
    def test_episode_payload(self):
        payload = {
            "name": "Mark Zuckerberg on Muse",
            "description": "desc",
            "release_date": "2026-09-08",
            "release_date_precision": "day",
            "duration_ms": 4_210_500,
            "images": [{"url": "https://i.scdn.co/big.jpg"}],
            "show": {"name": "Sources with Alex Heath", "publisher": "Alex Heath"},
        }
        meta = episode_metadata_from_api(payload, _EPISODE_ID)
        assert meta.source == "api"
        assert meta.publisher == "Alex Heath"
        assert meta.duration_seconds == 4210
        assert meta.release_date == datetime(2026, 9, 8, tzinfo=timezone.utc)
        assert meta.image_url == "https://i.scdn.co/big.jpg"

    def test_episode_payload_month_precision_pads_day(self):
        meta = episode_metadata_from_api({"name": "x", "release_date": "2026-09", "show": {"name": "s"}}, _EPISODE_ID)
        assert meta.release_date == datetime(2026, 9, 1, tzinfo=timezone.utc)

    def test_episode_payload_missing_show_raises(self):
        with pytest.raises(SpotifyResolutionError):
            episode_metadata_from_api({"name": "x"}, _EPISODE_ID)

    def test_show_payload(self):
        meta = show_metadata_from_api({"name": "S", "publisher": "P", "images": []}, _SHOW_ID)
        assert (meta.name, meta.publisher, meta.image_url, meta.source) == ("S", "P", None, "api")


# ---------------------------------------------------------------------------
# Text similarity
# ---------------------------------------------------------------------------


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Ep. 123 – The Big One!", "the big one"),
            ("#45: Café & Crème 🎧", "cafe and creme"),
            ("Episode 7 | Hello World", "hello world"),
            ("S2 E4 - Return", "return"),
            ("2024 Year in Review", "2024 year in review"),
            ("Episode 12", "episode 12"),  # marker-only title keeps its marker
            ("ゆる言語学ラジオ", "ゆる言語学ラジオ"),
            ("#12 Подкаст о технологиях!", "подкаст о технологиях"),
            ("snake_case title", "snake case title"),
            ("", ""),
        ],
    )
    def test_normalise_title(self, raw, expected):
        assert normalise_title(raw) == expected

    def test_token_set_ratio_ignores_order_and_prefix_noise(self):
        assert token_set_ratio("Ep 12: Muse and Meta", "Meta and Muse") == 1.0
        assert token_set_ratio("Completely different", "Nothing alike here") < 0.5
        assert token_set_ratio("", "x") == 0.0

    def test_token_set_ratio_penalises_unmatched_tokens(self):
        # A subset is evidence, not a perfect match (fuzzywuzzy scores it 1.0).
        assert token_set_ratio("Alpha Beta Gamma", "Alpha Beta Gamma Delta Epsilon") == pytest.approx(0.6)
        assert token_set_ratio("The Daily", "Daily") == pytest.approx(0.5)
        assert token_set_ratio("The Daily", "The Daily Show: Ears Edition") < 0.5

    def test_token_set_ratio_tolerates_typos(self):
        assert 0.9 < token_set_ratio("Mark Zuckerberg on Muse", "Mark Zuckerburg on Muse") < 1.0

    def test_non_latin_titles_are_compared_not_discarded(self):
        assert token_set_ratio("ゆる言語学ラジオ", "ゆる言語学ラジオ") == 1.0
        assert token_set_ratio("ゆる言語学ラジオ", "コテンラジオ") < 0.75
        # The Latin fragment alone must not make two different titles equal.
        assert token_set_ratio("AI 日本語", "AI ニュース") < 1.0

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Episode 12: Weekly update", ("keyword", 12)),
            ("#45 – Hello", ("keyword", 45)),
            ("S2 E4 - Return", ("keyword", 4)),
            ("12: Weekly update", ("bare", 12)),
            ("2024 Year in Review", None),
            ("Weekly update", None),
        ],
    )
    def test_episode_number(self, raw, expected):
        assert episode_number(raw) == expected

    def test_blank_titles_never_match(self):
        assert token_set_ratio("", "") == 0.0
        assert token_set_ratio("🎧", "!!!") == 0.0


# ---------------------------------------------------------------------------
# Stage 2 — Apple show match
# ---------------------------------------------------------------------------


def _show_result(name, artist, cid=123, feed="https://feeds.example.com/show.xml"):
    return {
        "wrapperType": "track",
        "kind": "podcast",
        "collectionId": cid,
        "collectionName": name,
        "artistName": artist,
        "feedUrl": feed,
        "artworkUrl600": f"https://art.example.com/{cid}.jpg",
    }


class TestShowMatch:
    def test_scores_prefer_exact_name_and_publisher(self):
        exact = score_show_candidate(
            "Sources with Alex Heath", "Alex Heath", _show_result("Sources with Alex Heath", "Alex Heath")
        )
        near = score_show_candidate("Sources with Alex Heath", "Alex Heath", _show_result("Sources", "Someone Else"))
        assert exact == pytest.approx(1.0)
        assert near < exact

    def test_picks_best_of_search_results(self):
        calls = []

        def search(params):
            calls.append(parse_qs(params)["term"][0])
            return [
                _show_result("Sources of Power", "Other", cid=1),
                _show_result("Sources with Alex Heath", "Alex Heath", cid=2),
                {"collectionName": "No feed", "artistName": "x"},  # no feedUrl → skipped
            ]

        match = find_apple_show("Sources with Alex Heath", "Alex Heath", search=search)
        assert match.collection_id == "2"
        assert match.feed_url == "https://feeds.example.com/show.xml"
        assert match.image_url == "https://art.example.com/2.jpg"
        assert calls == ["Sources with Alex Heath"]  # high-confidence hit stops after the first term

    def test_retries_with_simplified_term(self):
        terms = []

        def search(params):
            term = parse_qs(params)["term"][0]
            terms.append(term)
            return [_show_result("The Rest Is Politics", "Goalhanger")] if term == "The Rest Is Politics" else []

        match = find_apple_show("The Rest Is Politics: US", None, search=search)
        assert match.name == "The Rest Is Politics"
        assert terms == ["The Rest Is Politics: US", "The Rest Is Politics"]

    def test_publisher_unknown_scores_on_name_only(self):
        match = find_apple_show("Hard Fork", None, search=lambda p: [_show_result("Hard Fork", "The New York Times")])
        assert match.publisher == "The New York Times"

    @pytest.mark.parametrize("order", [("Daily", "The Daily"), ("The Daily", "Daily")])
    def test_exact_name_beats_subset_regardless_of_apple_order(self, order):
        results = [
            _show_result(name, "x", cid=i, feed=f"https://feeds.example.com/{i}.xml") for i, name in enumerate(order)
        ]
        match = find_apple_show("The Daily", None, search=lambda p: results)
        assert match.name == "The Daily"

    def test_superset_name_is_not_a_match_for_an_exclusive(self):
        # "Science" (exclusive) must not silently become "Science Vs".
        results = [
            _show_result("Science Vs", "Spotify Studios", cid=1, feed="https://feeds.example.com/1.xml"),
            _show_result("Science Friday", "WNYC", cid=2, feed="https://feeds.example.com/2.xml"),
        ]
        with pytest.raises(SpotifyResolutionError, match="Could not find"):
            find_apple_show("Science", None, search=lambda p: results)

    def test_two_feeds_with_the_same_name_are_ambiguous(self):
        results = [
            _show_result("Science", "A", cid=1, feed="https://feeds.example.com/1.xml"),
            _show_result("Science", "B", cid=2, feed="https://feeds.example.com/2.xml"),
        ]
        with pytest.raises(SpotifyResolutionError, match="More than one show"):
            find_apple_show("Science", None, search=lambda p: results)
        # ...but the publisher settles it when Spotify supplies one.
        assert find_apple_show("Science", "B", search=lambda p: results).collection_id == "2"

    def test_same_feed_listed_twice_is_not_ambiguous(self):
        results = [_show_result("Hard Fork", "NYT", cid=1), _show_result("Hard Fork", "NYT", cid=1)]
        assert find_apple_show("Hard Fork", None, search=lambda p: results).name == "Hard Fork"

    def test_non_latin_show_name_resolves(self):
        match = find_apple_show("ゆる言語学ラジオ", None, search=lambda p: [_show_result("ゆる言語学ラジオ", "x")])
        assert match.score == 1.0

    def test_exclusive_show_not_found_is_a_clean_error(self):
        with pytest.raises(SpotifyResolutionError, match="Spotify exclusive"):
            find_apple_show(
                "Some Exclusive", "Spotify Studios", search=lambda p: [_show_result("Totally Unrelated", "x")]
            )

    def test_no_results_is_a_clean_error(self):
        with pytest.raises(SpotifyResolutionError, match="Could not find"):
            find_apple_show("Nothing", None, search=lambda p: [])


# ---------------------------------------------------------------------------
# Stage 3 — episode match
# ---------------------------------------------------------------------------


class TestEpisodeScoring:
    def test_exact_match_scores_full(self):
        score = score_episode_candidate(_meta(), _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet"))
        assert score.combined == pytest.approx(1.0)
        assert score.accepted

    def test_ad_insertion_duration_drift_is_only_a_tiebreaker(self):
        # Apple copy runs 9 min longer (dynamic ads); title + date carry it.
        score = score_episode_candidate(
            _meta(), _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet", duration=4210 + 540)
        )
        assert score.duration == 0.6
        assert score.accepted

    def test_timezone_and_publish_lag_absorbed(self):
        score = score_episode_candidate(
            _meta(), _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet", hours=-30)
        )
        assert score.date == 1.0

    def test_exact_title_far_date_accepted_when_duration_agrees(self):
        # Show uploaded to Spotify weeks after Apple — exact title + duration is enough.
        score = score_episode_candidate(
            _meta(), _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet", hours=24 * 40)
        )
        assert score.date == 0.0
        assert score.accepted

    def test_unrelated_episode_rejected(self):
        score = score_episode_candidate(_meta(), _candidate("The Friday News Roundup", hours=24 * 3, duration=1800))
        assert not score.accepted
        assert score.combined < EPISODE_ACCEPT_THRESHOLD

    def test_missing_signals_are_neutral(self):
        score = score_episode_candidate(
            _meta(release_date=None, duration_seconds=None),
            EpisodeCandidate(title="Mark Zuckerberg on Muse, Meta's biggest AI bet yet", audio_url="u"),
        )
        assert (score.date, score.duration) == (0.5, 0.5)
        assert score.accepted

    def test_pick_prefers_best_and_ignores_candidates_without_audio(self):
        picked = pick_episode(
            _meta(),
            [
                _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet", audio="", hours=0),
                _candidate("Mark Zuckerberg on Muse (teaser)", hours=-48, duration=120, audio="https://cdn/teaser.mp3"),
                _candidate("Mark Zuckerberg on Muse, Meta's biggest AI bet yet", audio="https://cdn/full.mp3"),
            ],
        )
        assert picked is not None
        assert picked[0].audio_url == "https://cdn/full.mp3"

    def test_pick_returns_none_when_nothing_clears_threshold(self):
        assert pick_episode(_meta(), [_candidate("Other", hours=100, duration=100)]) is None

    def test_pick_rejects_ambiguous_near_tie(self):
        meta = _meta(title="Interview Part", duration_seconds=None)
        picked = pick_episode(
            meta,
            [
                _candidate("Interview Part 1", duration=None, audio="a"),
                _candidate("Interview Part 2", duration=None, audio="b"),
            ],
        )
        assert picked is None

    def test_episode_number_separates_identically_titled_episodes(self):
        # No date / duration on either side: the number is the only signal.
        meta = _meta(title="Episode 12: Weekly update", release_date=None, duration_seconds=None)
        candidates = [
            EpisodeCandidate(title="Episode 13: Weekly update", audio_url="u13"),
            EpisodeCandidate(title="Episode 12: Weekly update", audio_url="u12"),
        ]
        picked = pick_episode(meta, candidates)
        assert picked is not None and picked[0].audio_url == "u12"
        assert not score_episode_candidate(meta, candidates[0]).accepted

    def test_same_title_a_day_apart_is_ambiguous(self):
        # Spotify dates are day-precision and ±36 h is "tight", so both score 1.0 on date.
        meta = _meta(title="Morning Briefing", duration_seconds=None)
        picked = pick_episode(
            meta,
            [
                _candidate("Morning Briefing", hours=29, duration=None, audio="next-day"),
                _candidate("Morning Briefing", hours=5, duration=None, audio="same-day"),
            ],
        )
        assert picked is None

    def test_same_episode_listed_twice_is_not_ambiguous(self):
        meta = _meta(title="Morning Briefing")
        picked = pick_episode(meta, [_candidate("Morning Briefing"), _candidate("Morning Briefing")])
        assert picked is not None

    def test_subset_title_without_corroboration_is_rejected(self):
        meta = _meta(title="Inflation", release_date=None, duration_seconds=None)
        assert pick_episode(meta, [EpisodeCandidate(title="Inflation, tariffs and the Fed", audio_url="z")]) is None

    def test_pick_breaks_tie_on_duration(self):
        meta = _meta(title="Interview Part", duration_seconds=3600)
        picked = pick_episode(
            meta,
            [
                _candidate("Interview Part 1", duration=3600, audio="a"),
                _candidate("Interview Part 2", duration=1200, audio="b"),
            ],
        )
        assert picked is not None and picked[0].audio_url == "a"


class TestCandidateAdapters:
    def test_itunes_entries_filtered_by_collection(self):
        results = [
            {"wrapperType": "track", "kind": "podcast", "collectionId": 1},
            {
                "wrapperType": "podcastEpisode",
                "collectionId": 1,
                "trackId": 11,
                "trackName": "Yes",
                "episodeUrl": "https://cdn/yes.mp3",
                "releaseDate": "2026-09-08T22:00:00Z",
                "trackTimeMillis": 4_200_000,
                "artworkUrl600": "https://art/600.jpg",
            },
            {"wrapperType": "podcastEpisode", "collectionId": 2, "trackName": "Other show", "episodeUrl": "u"},
            {"wrapperType": "podcastEpisode", "collectionId": 1, "trackName": "No audio"},
        ]
        cands = candidates_from_itunes(results, "1")
        assert [c.title for c in cands] == ["Yes"]
        assert cands[0].duration_seconds == 4200
        assert cands[0].external_id == "11"
        assert cands[0].pub_date == datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc)
        assert cands[0].origin == "itunes"

    def test_feed_items(self):
        feed = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0"><channel><title>S</title>
<item><title>Deep Episode</title><guid>guid-1</guid><pubDate>Mon, 08 Sep 2026 22:02:00 GMT</pubDate>
<enclosure url="https://cdn/deep.mp3" type="audio/mpeg" length="1"/><itunes:duration>01:10:10</itunes:duration>
<description>notes</description></item>
<item><title>No enclosure</title></item>
</channel></rss>"""
        cands = candidates_from_feed(feed)
        assert len(cands) == 1
        assert cands[0].title == "Deep Episode"
        assert cands[0].audio_url == "https://cdn/deep.mp3"
        assert cands[0].duration_seconds == 4210
        assert cands[0].external_id == "guid-1"
        assert cands[0].pub_date == _RELEASE
        assert cands[0].origin == "rss"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _itunes_episode(title, *, cid=2, track=99, audio="https://cdn.example.com/full.mp3", millis=4_210_000):
    return {
        "wrapperType": "podcastEpisode",
        "collectionId": cid,
        "trackId": track,
        "trackName": title,
        "episodeUrl": audio,
        "releaseDate": "2026-09-08T22:00:00Z",
        "trackTimeMillis": millis,
        "description": "from itunes",
    }


class _Net:
    """Scriptable collaborators + call log."""

    def __init__(self, *, window=None, search_eps=None, feed=b"", show_results=None, meta=None):
        self.window = window or []
        self.search_eps = search_eps or []
        self.feed = feed
        self.show_results = (
            show_results if show_results is not None else [_show_result("Sources with Alex Heath", "Alex Heath", cid=2)]
        )
        self.meta = meta or _meta()
        self.calls = []

    def episode_metadata(self, episode_id, page_url):
        self.calls.append(("episode_meta", episode_id, page_url))
        return self.meta

    def show_metadata(self, show_id, page_url):
        self.calls.append(("show_meta", show_id, page_url))
        return SpotifyShowMetadata(show_id=show_id, name="Sources with Alex Heath", publisher="Alex Heath")

    def search(self, params):
        q = parse_qs(params)
        self.calls.append(("search", q.get("entity", [""])[0]))
        return self.show_results if q.get("entity") == ["podcast"] else self.search_eps

    def lookup(self, params):
        self.calls.append(("lookup", params))
        return self.window

    def fetch_feed(self, url):
        self.calls.append(("feed", url))
        return self.feed

    def expand(self, url):
        self.calls.append(("expand", url))
        return _EPISODE_URL

    def resolver(self):
        return SpotifyLinkResolver(
            episode_metadata=self.episode_metadata,
            show_metadata=self.show_metadata,
            search=self.search,
            lookup=self.lookup,
            feed=self.fetch_feed,
            expand=self.expand,
        )


class TestResolveEpisode:
    def test_tier1_itunes_window(self):
        net = _Net(window=[_itunes_episode("Mark Zuckerberg on Muse, Meta's biggest AI bet yet")])
        resolved = net.resolver().resolve_episode(_EPISODE_URL)
        assert resolved.episode_id == _EPISODE_ID
        assert resolved.show.feed_url == "https://feeds.example.com/show.xml"
        assert resolved.episode.audio_url == "https://cdn.example.com/full.mp3"
        assert resolved.episode.origin == "itunes"
        assert resolved.score.accepted
        kinds = [c[0] for c in net.calls]
        assert kinds == ["episode_meta", "search", "lookup"]
        # Page URL handed to the metadata fetcher is canonical (no ?si= tracking).
        assert net.calls[0][2] == f"https://open.spotify.com/episode/{_EPISODE_ID}"

    def test_same_named_feeds_are_settled_by_the_episode(self):
        # "The Daily" is two feeds in Apple's directory; an episode link has
        # no publisher to split them, but only one feed carries the episode.
        title = "Mark Zuckerberg on Muse, Meta's biggest AI bet yet"
        net = _Net(
            show_results=[
                _show_result(
                    "Sources with Alex Heath", "Subscriber feed", cid=1, feed="https://feeds.example.com/sub.xml"
                ),
                _show_result("Sources with Alex Heath", "Alex Heath", cid=2, feed="https://feeds.example.com/pub.xml"),
            ],
            window=[_itunes_episode(title, cid=2)],  # lookup filters by collectionId → only show 2 matches
            meta=_meta(publisher=None),
        )
        resolved = net.resolver().resolve_episode(_EPISODE_URL)
        assert resolved.show.collection_id == "2"
        assert resolved.episode.title == title

        # The show link has no such second signal and stays ambiguous.
        with pytest.raises(SpotifyResolutionError, match="More than one show"):
            find_apple_show("Sources with Alex Heath", None, search=net.search)

    def test_tier2_itunes_episode_search_when_window_misses(self):
        net = _Net(
            window=[_itunes_episode("Recent unrelated", track=1)],
            search_eps=[
                _itunes_episode(
                    "Mark Zuckerberg on Muse, Meta's biggest AI bet yet", track=2, audio="https://cdn/deep.mp3"
                )
            ],
        )
        resolved = net.resolver().resolve_episode(_EPISODE_URL)
        assert resolved.episode.audio_url == "https://cdn/deep.mp3"
        assert [c[0] for c in net.calls] == ["episode_meta", "search", "lookup", "search"]

    def test_tier3_rss_when_itunes_misses(self):
        feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>S</title>
<item><title>Mark Zuckerberg on Muse, Meta's biggest AI bet yet</title><guid>g</guid>
<pubDate>Mon, 08 Sep 2026 22:02:00 GMT</pubDate><enclosure url="https://cdn/rss.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""
        net = _Net(window=[_itunes_episode("Unrelated", track=1)], feed=feed)
        resolved = net.resolver().resolve_episode(_EPISODE_URL)
        assert resolved.episode.audio_url == "https://cdn/rss.mp3"
        assert resolved.episode.origin == "rss"
        assert ("feed", "https://feeds.example.com/show.xml") in net.calls

    def test_itunes_errors_fall_through_to_rss(self):
        net = _Net(
            feed=b"""<rss version="2.0"><channel><item><title>Mark Zuckerberg on Muse, Meta's biggest AI bet yet</title>
<enclosure url="https://cdn/rss.mp3" type="audio/mpeg" length="1"/></item></channel></rss>"""
        )

        def boom(params):
            raise SpotifyResolutionError("iTunes lookup returned HTTP 503")

        resolver = SpotifyLinkResolver(
            episode_metadata=net.episode_metadata,
            show_metadata=net.show_metadata,
            search=net.search,
            lookup=boom,
            feed=net.fetch_feed,
            expand=net.expand,
        )
        assert resolver.resolve_episode(_EPISODE_URL).episode.audio_url == "https://cdn/rss.mp3"

    def test_unmatched_episode_names_show_and_title(self):
        net = _Net(window=[_itunes_episode("Unrelated", track=1)], feed=b"<rss><channel></channel></rss>")
        with pytest.raises(SpotifyResolutionError) as exc:
            net.resolver().resolve_episode(_EPISODE_URL)
        assert "Sources with Alex Heath" in str(exc.value)
        assert "Mark Zuckerberg on Muse" in str(exc.value)

    def test_exclusive_show_stops_before_episode_lookup(self):
        net = _Net(show_results=[])
        with pytest.raises(SpotifyResolutionError, match="Spotify exclusive"):
            net.resolver().resolve_episode(_EPISODE_URL)
        assert not any(c[0] == "lookup" for c in net.calls)

    def test_show_link_is_rejected_with_pointer_to_add_podcast(self):
        with pytest.raises(SpotifyResolutionError, match="show link, not an episode"):
            _Net().resolver().resolve_episode(_SHOW_URL)

    def test_non_entity_spotify_link_is_rejected(self):
        with pytest.raises(SpotifyResolutionError, match="does not point to an episode or show"):
            _Net().resolver().resolve_episode("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")

    def test_spotify_uri_and_locale_paths_accepted(self):
        net = _Net(window=[_itunes_episode("Mark Zuckerberg on Muse, Meta's biggest AI bet yet")])
        assert net.resolver().resolve_episode(f"spotify:episode:{_EPISODE_ID}").episode_id == _EPISODE_ID
        assert (
            net.resolver().resolve_episode(f"https://open.spotify.com/intl-de/episode/{_EPISODE_ID}").episode_id
            == _EPISODE_ID
        )

    def test_short_link_is_expanded_first(self):
        net = _Net(window=[_itunes_episode("Mark Zuckerberg on Muse, Meta's biggest AI bet yet")])
        resolved = net.resolver().resolve_episode("https://spotify.link/AbC12xyz")
        assert resolved.episode_id == _EPISODE_ID
        assert net.calls[0] == ("expand", "https://spotify.link/AbC12xyz")


class TestResolveShow:
    def test_show_link(self):
        net = _Net()
        match = net.resolver().resolve_show(_SHOW_URL)
        assert isinstance(match, AppleShowMatch)
        assert match.feed_url == "https://feeds.example.com/show.xml"
        assert net.calls[0] == ("show_meta", _SHOW_ID, f"https://open.spotify.com/show/{_SHOW_ID}")

    def test_episode_link_resolves_to_its_show(self):
        net = _Net()
        match = net.resolver().resolve_show(_EPISODE_URL)
        assert match.collection_id == "2"
        assert net.calls[0][0] == "episode_meta"

    def test_exclusive_show_raises(self):
        with pytest.raises(SpotifyResolutionError, match="Spotify exclusive"):
            _Net(show_results=[]).resolver().resolve_show(_SHOW_URL)
