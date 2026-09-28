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
    def __init__(self, candidates: List[PlatformLinkCandidate], apple_url: Optional[str] = SHOW_URL):
        self.candidates = candidates
        self.apple_url = apple_url
        self.candidate_calls: List[Dict] = []
        self.upserts: List[List[PlatformLink]] = []
        self.set_apple_urls: List[str] = []

    def get_platform_link_candidates(self, podcast_id, platform, *, window, recheck_before):
        self.candidate_calls.append({"platform": platform, "window": window, "recheck_before": recheck_before})
        return list(self.candidates)

    def sync_podcast_chart_urls(self, podcast_id):
        return {"apple_url": self.apple_url, "youtube_url": None}

    def set_podcast_apple_url(self, podcast_id, apple_url):
        self.set_apple_urls.append(apple_url)
        self.apple_url = apple_url

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


def _service(repo, lookup=None, search=None, recheck_hours=24):
    calls = {"lookup": [], "search": []}

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

    svc = PlatformLinkService(repo, recheck_hours=recheck_hours, lookup=_lookup, search=_search, clock=lambda: NOW)
    return svc, calls


class TestThrottle:
    def test_no_candidates_makes_no_request_and_writes_nothing(self):
        repo = FakeRepo([])
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g1")])
        outcome = svc.link_podcast(_podcast())
        assert outcome.skipped == "no_candidates"
        assert calls["lookup"] == [] and calls["search"] == []
        assert repo.upserts == []

    def test_recheck_window_is_passed_unless_forced(self):
        repo = FakeRepo([])
        svc, _ = _service(repo, recheck_hours=6)
        svc.link_podcast(_podcast())
        svc.link_podcast(_podcast(), force=True)
        assert repo.candidate_calls[0]["recheck_before"] == NOW - timedelta(hours=6)
        assert repo.candidate_calls[0]["window"] == 200
        assert repo.candidate_calls[1]["recheck_before"] is None


class TestResolution:
    def test_links_matches_and_marks_the_rest_not_found(self):
        repo = FakeRepo([_cand("ep-a", "g-a"), _cand("ep-b", "g-b"), _cand("ep-c", "g-c")])
        svc, calls = _service(
            repo, lookup=[{"wrapperType": "track"}, _episode_entry(1, "g-a"), _episode_entry(2, "g-c")]
        )
        outcome = svc.link_podcast(_podcast())

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
        outcome = svc.link_podcast(_podcast())
        assert outcome.skipped == "lookup_failed"
        assert repo.upserts == []

    def test_dry_run_fetches_but_writes_nothing(self):
        repo = FakeRepo([_cand("ep-a", "g-a")])
        svc, calls = _service(repo, lookup=[_episode_entry(1, "g-a")])
        outcome = svc.link_podcast(_podcast(), dry_run=True)
        assert outcome.linked == 1 and calls["lookup"]
        assert repo.upserts == []

    def test_link_all_covers_every_podcast(self):
        repo = FakeRepo([])
        svc, _ = _service(repo)
        outcomes = svc.link_all()
        assert [o.podcast_id for o in outcomes] == ["pod-1"]


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
        outcome = svc.link_podcast(_podcast())

        assert calls["search"] == ["term=Prof+G+Markets&media=podcast&entity=podcast&limit=25"]
        assert repo.set_apple_urls == [SHOW_URL]
        assert calls["lookup"] == ["id=1498802610&entity=podcastEpisode&limit=200"]
        assert outcome.linked == 1

    def test_no_show_match_marks_candidates_not_found(self):
        repo = FakeRepo([_cand("ep-a", "g-a"), _cand("ep-b", "g-b")], apple_url=None)
        svc, calls = _service(
            repo, search=[{"feedUrl": "https://elsewhere.example.com/rss", "collectionViewUrl": SHOW_URL}]
        )
        outcome = svc.link_podcast(_podcast())

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
        outcome = svc.link_podcast(_podcast())
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
        outcome = svc.link_podcast(_podcast())

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
        outcome = svc.link_podcast(_podcast())

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
        outcome = svc.link_podcast(_podcast())
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
        assert svc.link_podcast(_podcast(), dry_run=True).linked == 1
        assert repo.set_apple_urls == [] and repo.upserts == []


class TestPlatformLinkModel:
    def test_rejects_unknown_platform_and_method(self):
        with pytest.raises(ValueError):
            PlatformLink(episode_id="e", platform="mixcloud")
        with pytest.raises(ValueError):
            PlatformLink(episode_id="e", platform="apple", match_method="vibes")
