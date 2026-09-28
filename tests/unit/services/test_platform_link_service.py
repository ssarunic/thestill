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

"""Spec #87 — PlatformLinkService against a fake repository and fake iTunes fetchers."""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytest

from thestill.models.podcast import PlatformLink, PlatformLinkCandidate, Podcast
from thestill.services.platform_link_service import PlatformLinkService

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
SHOW_URL = "https://podcasts.apple.com/us/podcast/prof-g/id1498802610"


class FakeRepo:
    def __init__(self, candidates: List[PlatformLinkCandidate], apple_url: Optional[str] = SHOW_URL, youtube_url=None):
        self.candidates = candidates
        self.apple_url = apple_url
        self.youtube_url = youtube_url
        self.candidate_calls: List[Dict] = []
        self.upserts: List[List[PlatformLink]] = []
        self.set_apple_urls: List[str] = []
        self.set_platform_urls: List = []
        self.alternates: Dict[str, list] = {}
        self.other_candidates: Dict[str, List[PlatformLinkCandidate]] = {}

    def get_platform_link_candidates(self, podcast_id, platform, *, window, recheck_before):
        self.candidate_calls.append({"platform": platform, "window": window, "recheck_before": recheck_before})
        if platform == "apple":
            return list(self.candidates)
        return list(self.other_candidates.get(platform, []))

    def sync_podcast_chart_urls(self, podcast_id):
        return {"apple_url": self.apple_url, "youtube_url": self.youtube_url, "spotify_url": None}

    def set_podcast_platform_url(self, podcast_id, platform, url):
        if platform == "apple":
            self.set_apple_urls.append(url)
            self.apple_url = url
        else:
            self.set_platform_urls.append((platform, url))

    def get_alternate_enclosures_for_episodes(self, episode_ids):
        return {eid: self.alternates.get(eid, []) for eid in episode_ids}

    def upsert_platform_links(self, links):
        self.upserts.append(list(links))
        return len(links)

    def get_all(self):
        return [_podcast()]


def _podcast() -> Podcast:
    return Podcast(id="pod-1", rss_url="https://feeds.example.com/profg/rss", title="Prof G Markets", description="d")


def _cand(episode_id: str, external_id: str) -> PlatformLinkCandidate:
    return PlatformLinkCandidate(
        episode_id=episode_id, external_id=external_id, audio_url=f"https://a/{episode_id}.mp3", title=episode_id
    )


def _episode_entry(track_id: int, guid: str) -> dict:
    return {
        "wrapperType": "podcastEpisode",
        "trackId": track_id,
        "trackViewUrl": f"{SHOW_URL}?i={track_id}",
        "episodeGuid": guid,
    }


def _service(repo, lookup=None, search=None, videos=None, recheck_hours=24):
    calls = {"lookup": [], "search": [], "videos": []}

    def _lookup(params):
        calls["lookup"].append(params)
        if isinstance(lookup, Exception):
            raise lookup
        return lookup or []

    def _search(params):
        calls["search"].append(params)
        if isinstance(search, Exception):
            raise search
        return search or []

    def _videos(channel_url, *, limit):
        calls["videos"].append((channel_url, limit))
        if isinstance(videos, Exception):
            raise videos
        return videos or []

    svc = PlatformLinkService(
        repo, recheck_hours=recheck_hours, lookup=_lookup, search=_search, list_videos=_videos, clock=lambda: NOW
    )
    return svc, calls


class TestThrottle:
    def test_no_candidates_makes_no_request_and_writes_nothing(self):
        repo = FakeRepo([])
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g1")])
        report = svc.link_podcast(_podcast())
        assert [o.skipped for o in report.outcomes] == ["no_candidates"] * 3
        assert calls["lookup"] == [] and calls["search"] == [] and calls["videos"] == []
        assert repo.upserts == []

    def test_recheck_window_is_passed_unless_forced(self):
        repo = FakeRepo([])
        svc, _ = _service(repo, recheck_hours=6)
        svc.link_podcast(_podcast())
        svc.link_podcast(_podcast(), force=True)
        first_pass, forced_pass = repo.candidate_calls[:3], repo.candidate_calls[3:]
        assert [c["platform"] for c in first_pass] == ["apple", "spotify", "youtube"]
        assert all(c["recheck_before"] == NOW - timedelta(hours=6) and c["window"] == 200 for c in first_pass)
        assert all(c["recheck_before"] is None for c in forced_pass)


class TestResolution:
    def test_links_matches_and_marks_the_rest_not_found(self):
        repo = FakeRepo([_cand("ep-a", "g-a"), _cand("ep-b", "g-b"), _cand("ep-c", "g-c")])
        svc, calls = _service(
            repo, lookup=[{"wrapperType": "track"}, _episode_entry(1, "g-a"), _episode_entry(2, "g-c")]
        )
        outcome = svc.link_podcast(_podcast()).outcomes[0]

        assert calls["lookup"] == ["id=1498802610&entity=podcastEpisode&limit=200"]
        assert calls["search"] == []
        assert (outcome.linked, outcome.not_found, outcome.candidates, outcome.skipped) == (2, 1, 3, None)
        assert outcome.matched_by == {"guid": 2}
        rows = {r.episode_id: r for r in repo.upserts[0]}
        assert rows["ep-a"].url == f"{SHOW_URL}?i=1" and rows["ep-a"].external_ref == "1"
        assert rows["ep-a"].match_method == "guid" and rows["ep-a"].checked_at == NOW
        assert rows["ep-b"].url is None and rows["ep-b"].match_method is None and rows["ep-b"].checked_at == NOW
        assert rows["ep-c"].url == f"{SHOW_URL}?i=2"

    def test_lookup_failure_writes_nothing(self):
        repo = FakeRepo([_cand("ep-a", "g-a")])
        svc, _ = _service(repo, lookup=RuntimeError("iTunes lookup returned HTTP 503"))
        outcome = svc.link_podcast(_podcast()).outcomes[0]
        assert outcome.skipped == "lookup_failed"
        assert repo.upserts == []

    def test_dry_run_fetches_but_writes_nothing(self):
        repo = FakeRepo([_cand("ep-a", "g-a")])
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g-a")])
        outcome = svc.link_podcast(_podcast(), dry_run=True).outcomes[0]
        assert outcome.linked == 1 and calls["lookup"]
        assert repo.upserts == []

    def test_link_all_covers_every_podcast(self):
        repo = FakeRepo([])
        svc, _ = _service(repo)
        outcomes = svc.link_all()
        assert [r.podcast_id for r in outcomes] == ["pod-1"]
        assert [o.platform for o in outcomes[0].outcomes] == ["apple", "spotify", "youtube"]


class TestShowDiscovery:
    def test_search_accepted_only_on_feed_url_equality(self):
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        search = [
            {
                "feedUrl": "https://feeds.example.com/other/rss",
                "collectionViewUrl": SHOW_URL.replace("1498802610", "1"),
            },
            {
                "feedUrl": "http://FEEDS.example.com/profg/rss/",
                "collectionViewUrl": SHOW_URL,
                "collectionId": 1498802610,
            },
        ]
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g-a")], search=search)
        outcome = svc.link_podcast(_podcast()).outcomes[0]

        assert calls["search"] == ["term=Prof+G+Markets&media=podcast&entity=podcast&limit=25"]
        assert repo.set_apple_urls == [SHOW_URL]
        assert calls["lookup"] == ["id=1498802610&entity=podcastEpisode&limit=200"]
        assert outcome.linked == 1

    def test_no_show_match_marks_candidates_not_found(self):
        repo = FakeRepo([_cand("ep-a", "g-a"), _cand("ep-b", "g-b")], apple_url=None)
        svc, calls = _service(
            repo, search=[{"feedUrl": "https://elsewhere.example.com/rss", "collectionViewUrl": SHOW_URL}]
        )
        outcome = svc.link_podcast(_podcast()).outcomes[0]

        assert outcome.skipped == "no_apple_id" and outcome.not_found == 2
        assert calls["lookup"] == []
        assert repo.set_apple_urls == []
        assert [(r.episode_id, r.url, r.checked_at) for r in repo.upserts[0]] == [
            ("ep-a", None, NOW),
            ("ep-b", None, NOW),
        ]

    def test_search_failure_marks_not_found_so_it_is_retried_after_recheck(self):
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        svc, _ = _service(repo, search=RuntimeError("boom"))
        outcome = svc.link_podcast(_podcast()).outcomes[0]
        assert outcome.skipped == "no_apple_id"
        assert len(repo.upserts) == 1

    def test_unique_title_hit_is_kept_only_when_the_window_proves_it(self):
        # Same show on another feed host: feedUrl differs, title matches once.
        search = [
            {
                "feedUrl": "https://anchor.fm/s/abc/podcast/rss",
                "collectionName": "Prof G Markets",
                "collectionViewUrl": f"{SHOW_URL}?uo=4",
            },
            {
                "feedUrl": "https://other.example.com/rss",
                "collectionName": "Prof G Markets Recap",
                "collectionViewUrl": SHOW_URL.replace("1498802610", "9"),
            },
        ]
        repo = FakeRepo([_cand("ep-a", "g-a"), _cand("ep-b", "g-b")], apple_url=None)
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g-a")], search=search)
        outcome = svc.link_podcast(_podcast()).outcomes[0]

        assert calls["lookup"] == ["id=1498802610&entity=podcastEpisode&limit=200"]
        assert repo.set_apple_urls == [SHOW_URL]  # tracking query stripped, stored after proof
        assert (outcome.linked, outcome.not_found, outcome.skipped) == (1, 1, None)

    def test_unique_title_hit_without_guid_proof_stores_nothing(self):
        search = [
            {"feedUrl": "https://anchor.fm/x/rss", "collectionName": "Prof G Markets", "collectionViewUrl": SHOW_URL}
        ]
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        # The window only offers a same-title entry — a namesake show would too.
        window = [{**_episode_entry(1, "other-guid"), "trackName": "ep-a", "releaseDate": "2026-09-28T12:00:00Z"}]
        svc, calls = _service(repo, lookup=window, search=search)
        outcome = svc.link_podcast(_podcast()).outcomes[0]

        assert calls["lookup"]  # the window was fetched to test the hit
        assert repo.set_apple_urls == []
        assert outcome.skipped == "no_apple_id" and outcome.not_found == 1
        assert [r.url for r in repo.upserts[0]] == [None]

    def test_two_title_hits_are_ambiguous(self):
        search = [
            {"feedUrl": "https://a/rss", "collectionName": "Prof G Markets", "collectionViewUrl": SHOW_URL},
            {
                "feedUrl": "https://b/rss",
                "collectionName": "prof g markets",
                "collectionViewUrl": SHOW_URL.replace("1498802610", "9"),
            },
        ]
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g-a")], search=search)
        outcome = svc.link_podcast(_podcast()).outcomes[0]
        assert calls["lookup"] == [] and outcome.skipped == "no_apple_id"

    def test_dry_run_never_stores_the_show_url(self):
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        search = [{"feedUrl": "https://feeds.example.com/profg/rss", "collectionViewUrl": SHOW_URL}]
        svc, _ = _service(repo, lookup=[_episode_entry(1, "g-a")], search=search)
        svc.link_podcast(_podcast(), dry_run=True)
        assert repo.set_apple_urls == [] and repo.upserts == []

        # Same for the window-proven title route.
        repo = FakeRepo([_cand("ep-a", "g-a")], apple_url=None)
        search = [
            {"feedUrl": "https://anchor.fm/x/rss", "collectionName": "Prof G Markets", "collectionViewUrl": SHOW_URL}
        ]
        svc, _ = _service(repo, lookup=[_episode_entry(1, "g-a")], search=search)
        assert svc.link_podcast(_podcast(), dry_run=True).outcomes[0].linked == 1
        assert repo.set_apple_urls == [] and repo.upserts == []


class TestPlatformLinkModel:
    def test_rejects_unknown_platform_and_method(self):
        with pytest.raises(ValueError):
            PlatformLink(episode_id="e", platform="mixcloud")
        with pytest.raises(ValueError):
            PlatformLink(episode_id="e", platform="apple", match_method="vibes")


def _ocand(
    episode_id, *, title="Ep", pub_date=NOW, duration=None, description_html="", website_url=None, canonical_id=None
):
    return PlatformLinkCandidate(
        episode_id=episode_id,
        external_id=f"g-{episode_id}",
        audio_url=f"https://a/{episode_id}.mp3",
        title=title,
        pub_date=pub_date,
        duration=duration,
        description_html=description_html,
        website_url=website_url,
        canonical_id=canonical_id,
    )


def _rows(repo, platform):
    return {r.episode_id: r for batch in repo.upserts for r in batch if r.platform == platform}


class TestSpotifyPublisherLinks:
    def test_unique_description_link_and_item_link_are_taken_the_rest_marked_not_found(self):
        repo = FakeRepo([])
        repo.other_candidates["spotify"] = [
            _ocand(
                "d",
                description_html='<a href="https://open.spotify.com/episode/0tkEdaVIsQNGKUGUKTPqeH?si=1">listen</a>',
            ),
            _ocand("w", website_url="https://open.spotify.com/episode/1AAAAAAAAAAAAAAAAAAAAA"),
            _ocand(
                "two",
                description_html="open.spotify.com/episode/2BBBBBBBBBBBBBBBBBBBBB and open.spotify.com/episode/3CCCCCCCCCCCCCCCCCCCCC",
            ),
            _ocand("none"),
        ]
        svc, _ = _service(repo)
        spotify = svc.link_podcast(_podcast()).outcomes[1]

        assert (spotify.platform, spotify.linked, spotify.not_found, spotify.matched_by) == (
            "spotify",
            2,
            2,
            {"publisher": 2},
        )
        rows = _rows(repo, "spotify")
        assert rows["d"].url == "https://open.spotify.com/episode/0tkEdaVIsQNGKUGUKTPqeH"
        assert rows["d"].match_method == "publisher" and rows["d"].external_ref == "0tkEdaVIsQNGKUGUKTPqeH"
        assert rows["w"].url == "https://open.spotify.com/episode/1AAAAAAAAAAAAAAAAAAAAA"
        assert rows["two"].url is None and rows["none"].url is None

    def test_import_canonical_id_wins(self):
        repo = FakeRepo([])
        repo.other_candidates["spotify"] = [_ocand("i", canonical_id="spotify:4DDDDDDDDDDDDDDDDDDDDD")]
        svc, _ = _service(repo)
        svc.link_podcast(_podcast())
        assert _rows(repo, "spotify")["i"].external_ref == "4DDDDDDDDDDDDDDDDDDDDD"

    def test_show_link_in_podcast_description_is_stored_once(self):
        repo = FakeRepo([])
        repo.other_candidates["spotify"] = [_ocand("x")]
        svc, _ = _service(repo)
        podcast = _podcast()
        podcast.description = "Also on https://open.spotify.com/show/2MAi0BvDc6GTFvKFPXnkCL"
        svc.link_podcast(podcast)
        assert repo.set_platform_urls == [("spotify", "https://open.spotify.com/show/2MAi0BvDc6GTFvKFPXnkCL")]
        svc.link_podcast(podcast, dry_run=True)
        assert len(repo.set_platform_urls) == 1  # dry run stores nothing


def _video(video_id, title, uploaded=NOW, duration=3600):
    from thestill.core.youtube_episode_linker import YouTubeVideoEntry

    return YouTubeVideoEntry(video_id=video_id, title=title, uploaded=uploaded, duration=duration)


class TestYouTube:
    def test_publisher_links_then_channel_listing(self):
        from thestill.models.podcast import AlternateEnclosure

        repo = FakeRepo([], youtube_url="https://www.youtube.com/@profgmarkets")
        repo.other_candidates["youtube"] = [
            _ocand("alt", title="Alt ep"),
            _ocand("chan", title="Markets Weekly: Tariffs", duration=3600),
            _ocand("miss", title="Nothing on YouTube", duration=3600),
        ]
        repo.alternates["alt"] = [
            AlternateEnclosure(
                episode_id="alt", source_uri="https://www.youtube.com/watch?v=aaaaaaaaaaa", mime_type="video/youtube"
            )
        ]
        videos = [
            _video("bbbbbbbbbbb", "Markets Weekly: Tariffs", duration=3700),
            _video("ccccccccccc", "Markets Weekly: Tariffs — CLIP", duration=300),
        ]
        svc, calls = _service(repo, videos=videos)
        yt = svc.link_podcast(_podcast()).outcomes[2]

        assert calls["videos"] == [("https://www.youtube.com/@profgmarkets", 300)]
        assert (yt.linked, yt.not_found, yt.skipped) == (2, 1, None)
        assert yt.matched_by == {"publisher": 1, "title_date": 1}
        rows = _rows(repo, "youtube")
        assert rows["alt"].external_ref == "aaaaaaaaaaa" and rows["alt"].match_method == "publisher"
        assert rows["chan"].url == "https://www.youtube.com/watch?v=bbbbbbbbbbb"
        assert rows["miss"].url is None

    def test_listing_is_skipped_when_the_feed_covered_everything(self):
        repo = FakeRepo([], youtube_url="https://www.youtube.com/@x")
        repo.other_candidates["youtube"] = [_ocand("i", canonical_id="youtube:aaaaaaaaaaa")]
        svc, calls = _service(repo, videos=RuntimeError("must not be called"))
        yt = svc.link_podcast(_podcast()).outcomes[2]
        assert calls["videos"] == [] and yt.linked == 1

    def test_no_channel_marks_not_found(self):
        repo = FakeRepo([])
        repo.other_candidates["youtube"] = [_ocand("a"), _ocand("b")]
        svc, calls = _service(repo)
        yt = svc.link_podcast(_podcast()).outcomes[2]
        assert (yt.skipped, yt.not_found) == ("no_channel", 2)
        assert calls["videos"] == []
        assert {r.episode_id for r in _rows(repo, "youtube").values()} == {"a", "b"}

    def test_listing_failure_writes_nothing_for_the_unresolved(self):
        repo = FakeRepo([], youtube_url="https://www.youtube.com/@x")
        repo.other_candidates["youtube"] = [_ocand("pub", canonical_id="youtube:aaaaaaaaaaa"), _ocand("rest")]
        svc, _ = _service(repo, videos=RuntimeError("Sign in to confirm you're not a bot"))
        yt = svc.link_podcast(_podcast()).outcomes[2]
        assert (yt.skipped, yt.linked, yt.not_found) == ("lookup_failed", 1, 0)
        assert list(_rows(repo, "youtube")) == ["pub"]

    def test_channel_discovered_from_publisher_votes_is_stored_and_used(self):
        repo = FakeRepo([])
        text = 'Video: <a href="https://www.youtube.com/@profgmarkets">YouTube</a>'
        repo.other_candidates["youtube"] = [_ocand(f"e{i}", title=f"Show {i}", description_html=text) for i in range(3)]
        svc, calls = _service(repo, videos=[])
        svc.link_podcast(_podcast())
        assert repo.set_platform_urls == [("youtube", "https://www.youtube.com/@profgmarkets")]
        assert calls["videos"] == [("https://www.youtube.com/@profgmarkets", 300)]
