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

"""End-to-end ImportService tests for Apple Podcasts imports.

The Apple resolver emits a ``CanonicalParent``, so the import flow
upserts the show as an ``auto_added`` podcast row that stays out of
refresh and discovery until at least one user follows it.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from thestill.core.queue_manager import QueueManager
from thestill.models.user import User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services import import_service
from thestill.services.import_service import (
    ApplePodcastsResolver,
    ImportService,
    ResolverError,
    _apple_page_episode_lookup,
    _apple_rss_cross_match,
    _default_apple_episode_lookup,
)

_APPLE_URL = "https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000"

# Trimmed capture (2026-07-25) of the real episode page that motivated
# spec #65 — an episode deeper than the iTunes API's 200-episode window.
# Contains the target node plus a decoy with a different id.
_PAGE_FIXTURE = Path(__file__).parents[2] / "fixtures" / "apple" / "storonsky-episode-page.html"
_DEEP_TRACK_ID = "1000678898255"
_DEEP_DECOY_ID = "1000600000001"
_DEEP_URL = f"https://podcasts.apple.com/gb/podcast/20vc/id958230465?i={_DEEP_TRACK_ID}"


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "imports_apple.db")
    SqlitePodcastRepository(path)
    return path


@pytest.fixture
def repo(db_path):
    return SqlitePodcastRepository(db_path)


@pytest.fixture
def inbox_repo(db_path):
    return SqliteInboxRepository(db_path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def queue(db_path):
    return QueueManager(db_path)


def _service_with(repo, inbox_repo, queue, info):
    return ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda track_id, collection_id=None, page_url=None: info)],
    )


def _make_user(user_repo, email):
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def test_apple_import_upserts_show_as_auto_added(repo, inbox_repo, queue, user_repo, db_path, fake_apple_episode_info):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_apple_episode_info)

    result = svc.import_url(user_id=alice.id, url=_APPLE_URL)

    assert result.episode_created
    assert result.canonical_id == "apple:1000620312000"
    assert result.kind == "apple_episode"
    # Parent metadata threaded through ImportResult — no second DB lookup.
    assert result.parent_title == "The Daily"
    assert result.parent_slug == "the-daily"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, title, slug, synthetic, auto_added FROM podcasts WHERE rss_url = ?",
            ("https://feeds.example.com/the-daily",),
        ).fetchone()
        assert row is not None
        assert row["auto_added"] == 1
        assert row["synthetic"] == 0
        assert row["title"] == "The Daily"
        assert row["slug"] == "the-daily"

        ep = conn.execute(
            "SELECT podcast_id, canonical_id, audio_url FROM episodes WHERE id = ?",
            (result.episode_id,),
        ).fetchone()
        assert ep["podcast_id"] == row["id"]
        assert ep["audio_url"] == "https://cdn.example.com/episode.mp3"


def test_apple_import_dedup_returns_existing_parent(repo, inbox_repo, queue, user_repo, fake_apple_episode_info):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_apple_episode_info)

    r1 = svc.import_url(user_id=alice.id, url=_APPLE_URL)
    r2 = svc.import_url(user_id=alice.id, url=_APPLE_URL)

    assert r1.episode_id == r2.episode_id
    assert r2.episode_created is False
    # Dedup hit still surfaces the parent so the modal CTA stays useful.
    assert r2.parent_title == "The Daily"
    assert r2.parent_slug == "the-daily"


def test_apple_import_increments_user_quota_counter(repo, inbox_repo, queue, user_repo, fake_apple_episode_info):
    """Each successful import is counted by ``count_imports_for_user_since``,
    which is the contract future quota enforcement will read off."""
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_apple_episode_info)
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    assert inbox_repo.count_imports_for_user_since(alice.id, since) == 0
    svc.import_url(user_id=alice.id, url=_APPLE_URL)
    assert inbox_repo.count_imports_for_user_since(alice.id, since) == 1

    # Second import by the same user → counter increments.
    second_url = _APPLE_URL.replace("1000620312000", "1000620312001")
    info2 = {**fake_apple_episode_info, "trackId": 1000620312001}
    svc2 = _service_with(repo, inbox_repo, queue, info2)
    svc2.import_url(user_id=alice.id, url=second_url)
    assert inbox_repo.count_imports_for_user_since(alice.id, since) == 2


def test_apple_import_ingests_full_feed_when_feed_manager_present(
    repo, inbox_repo, queue, user_repo, db_path, fake_apple_episode_info
):
    """With ``feed_manager`` injected, import triggers a one-shot RSS refresh
    on the auto-added parent so the show row gets its full episode list and
    the imported episode dedups against an RSS-discovered row by audio_url."""
    alice = _make_user(user_repo, "alice@example.com")

    # Stub feed_manager. The real one fetches the feed and inserts every
    # episode under the parent — we simulate that by inserting three rows
    # directly, including one whose audio_url matches the imported episode.
    class FakeFeedManager:
        def __init__(self, repo, parent_rss_url, imported_audio_url):
            self.calls = []
            self._repo = repo
            self._parent_rss_url = parent_rss_url
            self._imported_audio_url = imported_audio_url

        def get_new_episodes(self, *, podcast_id):
            self.calls.append(podcast_id)
            with sqlite3.connect(db_path) as conn:
                now = datetime.now(timezone.utc).isoformat()
                rows = [
                    ("rss-guid-1", "Episode 1", "episode-1", "https://cdn.example.com/older.mp3"),
                    # The match — same audio_url as the imported Apple track.
                    ("rss-guid-2", "The Friday News Roundup", "the-friday-news-roundup", self._imported_audio_url),
                    ("rss-guid-3", "Episode 3", "episode-3", "https://cdn.example.com/newer.mp3"),
                ]
                for ext_id, title, slug, audio_url in rows:
                    conn.execute(
                        """
                        INSERT INTO episodes (id, podcast_id, created_at, updated_at,
                                              external_id, title, slug, description,
                                              description_html, audio_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?)
                        """,
                        (str(uuid.uuid4()), podcast_id, now, now, ext_id, title, slug, audio_url),
                    )
                conn.commit()
            return []

    fake_fm = FakeFeedManager(repo, "https://feeds.example.com/the-daily", fake_apple_episode_info["episodeUrl"])
    svc = ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None, url=None: fake_apple_episode_info)],
        feed_manager=fake_fm,
    )

    result = svc.import_url(user_id=alice.id, url=_APPLE_URL)

    assert fake_fm.calls, "feed_manager should be refreshed for the auto-added parent"
    assert result.episode_created
    assert result.canonical_id == "apple:1000620312000"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        # 3 RSS rows, no extra synthetic single-episode row — the import
        # attached its canonical_id onto the matching RSS row.
        eps = conn.execute(
            "SELECT id, external_id, audio_url, canonical_id FROM episodes WHERE podcast_id = ("
            "  SELECT id FROM podcasts WHERE rss_url = ?)"
            "  ORDER BY external_id",
            ("https://feeds.example.com/the-daily",),
        ).fetchall()
        assert len(eps) == 3
        matched = [e for e in eps if e["audio_url"] == fake_apple_episode_info["episodeUrl"]]
        assert len(matched) == 1
        assert matched[0]["canonical_id"] == "apple:1000620312000"
        assert matched[0]["id"] == result.episode_id
        # Other RSS-discovered episodes do NOT get the canonical_id.
        for e in eps:
            if e["audio_url"] != fake_apple_episode_info["episodeUrl"]:
                assert e["canonical_id"] is None


def test_apple_import_falls_back_when_audio_url_not_in_feed(
    repo, inbox_repo, queue, user_repo, db_path, fake_apple_episode_info
):
    """RSS may legitimately not list every Apple-indexed episode. When the
    refresh succeeds but no row matches the imported audio_url, the import
    falls back to the single-episode insert path."""
    alice = _make_user(user_repo, "alice@example.com")

    class EmptyFeedManager:
        def get_new_episodes(self, *, podcast_id):
            # Refresh ran, but the episode the user pasted is not in the feed.
            return []

    svc = ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None, url=None: fake_apple_episode_info)],
        feed_manager=EmptyFeedManager(),
    )

    result = svc.import_url(user_id=alice.id, url=_APPLE_URL)
    assert result.episode_created
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        ep = conn.execute("SELECT canonical_id FROM episodes WHERE id = ?", (result.episode_id,)).fetchone()
        assert ep["canonical_id"] == "apple:1000620312000"


def test_apple_import_falls_back_when_feed_refresh_raises(
    repo, inbox_repo, queue, user_repo, db_path, fake_apple_episode_info
):
    """RSS fetch errors must not block imports — the single-episode path
    is the safety net."""
    alice = _make_user(user_repo, "alice@example.com")

    class BrokenFeedManager:
        def get_new_episodes(self, *, podcast_id):
            raise RuntimeError("feed unreachable")

    svc = ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None, url=None: fake_apple_episode_info)],
        feed_manager=BrokenFeedManager(),
    )

    result = svc.import_url(user_id=alice.id, url=_APPLE_URL)
    assert result.episode_created
    assert result.canonical_id == "apple:1000620312000"


# ============================================================================
# Tier 3 — episode-page scrape (spec #65)
# ============================================================================


def _page_html() -> str:
    return _PAGE_FIXTURE.read_text(encoding="utf-8")


def _html_with_payload(payload) -> str:
    return (
        '<html><body><script type="application/json" id="serialized-server-data">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


def _episode_node(**overrides) -> dict:
    node = {
        "id": _DEEP_TRACK_ID,
        "adamId": _DEEP_TRACK_ID,
        "title": "Deep Episode",
        "releaseDate": "2024-12-04T17:30:00Z",
        "summary": "About the episode.",
        "mediaEnclosures": [{"streamUrl": "https://cdn.example.com/deep.mp3", "duration": 100}],
    }
    node.update(overrides)
    return node


class TestApplePageEpisodeLookup:
    def test_happy_path_returns_itunes_shaped_dict(self):
        info = _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: _page_html())

        assert info["wrapperType"] == "podcastEpisode"
        assert info["trackId"] == _DEEP_TRACK_ID
        assert info["trackName"].startswith("20VC: Revolut Founder Nik Storonsky")
        assert info["episodeUrl"].startswith("https://traffic.libsyn.com/secure/thetwentyminutevc/")
        assert info["trackTimeMillis"] == 2753 * 1000
        assert info["releaseDate"] == "2024-12-04T17:30:00Z"
        assert info["description"].startswith("<p>Nik Storonsky")

    def test_selects_node_by_its_own_id_not_title(self):
        # The fixture's decoy node shares the target's title but has a
        # different id — selection must key on id/adamId, never title.
        info = _apple_page_episode_lookup(_DEEP_URL, _DEEP_DECOY_ID, fetch=lambda url: _page_html())
        assert info["trackId"] == _DEEP_DECOY_ID

    def test_show_record_fields_merged_for_parent_bootstrap(self):
        show = {
            "feedUrl": "https://feeds.example.com/20vc",
            "collectionName": "The Twenty Minute VC",
            "collectionId": 958230465,
            "artworkUrl600": "https://cdn.example.com/20vc-600.jpg",
        }
        info = _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, show_record=show, fetch=lambda url: _page_html())
        assert info["feedUrl"] == "https://feeds.example.com/20vc"
        assert info["collectionName"] == "The Twenty Minute VC"
        assert info["artworkUrl600"] == "https://cdn.example.com/20vc-600.jpg"

    def test_missing_payload_raises_with_rss_escape_hatch(self):
        with pytest.raises(ResolverError) as exc_info:
            _apple_page_episode_lookup(
                _DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: "<html><body>consent wall</body></html>"
            )
        assert "RSS feed" in str(exc_info.value)

    def test_non_json_payload_raises(self):
        html = '<script type="application/json" id="serialized-server-data">{broken</script>'
        with pytest.raises(ResolverError) as exc_info:
            _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: html)
        assert "RSS feed" in str(exc_info.value)

    def test_episode_node_not_found_raises(self):
        with pytest.raises(ResolverError) as exc_info:
            _apple_page_episode_lookup(_DEEP_URL, "999999", fetch=lambda url: _page_html())
        assert "999999" in str(exc_info.value)
        assert "RSS feed" in str(exc_info.value)

    def test_missing_stream_url_names_subscriber_only(self):
        html = _html_with_payload([_episode_node(mediaEnclosures=[{"priceType": "STDQ"}])])
        with pytest.raises(ResolverError) as exc_info:
            _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: html)
        assert "subscriber-only" in str(exc_info.value)

    def test_non_http_stream_url_rejected(self):
        node = _episode_node(mediaEnclosures=[{"streamUrl": "itms://not-a-web-url"}])
        with pytest.raises(ResolverError):
            _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: _html_with_payload([node]))

    def test_bad_optional_fields_dropped_not_fatal(self):
        node = _episode_node(releaseDate={"weird": True}, summary=None)
        node["mediaEnclosures"] = [{"streamUrl": "https://cdn.example.com/deep.mp3", "duration": "2753"}]
        info = _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=lambda url: _html_with_payload([node]))
        assert info["episodeUrl"] == "https://cdn.example.com/deep.mp3"
        assert "trackTimeMillis" not in info
        assert "releaseDate" not in info
        assert "description" not in info

    def test_fetch_failure_wrapped_with_escape_hatch(self):
        def boom(url):
            raise ResolverError("Apple episode page returned HTTP 404")

        with pytest.raises(ResolverError) as exc_info:
            _apple_page_episode_lookup(_DEEP_URL, _DEEP_TRACK_ID, fetch=boom)
        assert "HTTP 404" in str(exc_info.value)
        assert "RSS feed" in str(exc_info.value)


# ============================================================================
# Tier 3 — RSS cross-match (spec #65)
# ============================================================================


def _feed_xml(items) -> bytes:
    body = "".join(
        f"<item><title>{title}</title><pubDate>{pub}</pubDate>"
        f'<enclosure url="{url}" type="audio/mpeg" length="1"/></item>'
        for title, pub, url in items
    )
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>Show</title>' + body + "</channel></rss>").encode()


_SCRAPED = {
    "trackName": "Deep Episode",
    "episodeUrl": "https://cdn-a.example.com/shows/deep.mp3?scrape-token=1",
    "releaseDate": "2024-12-04T17:30:00Z",
}


class TestAppleRssCrossMatch:
    def test_matches_by_enclosure_path_ignoring_host_and_query(self):
        feed = _feed_xml(
            [
                ("Other", "Tue, 03 Dec 2024 10:00:00 GMT", "https://cdn-b.example.com/shows/other.mp3"),
                (
                    "Renamed Title",
                    "Wed, 04 Dec 2024 17:30:00 GMT",
                    "https://cdn-b.example.com/shows/deep.mp3?feed-token=2",
                ),
            ]
        )
        override = _apple_rss_cross_match("https://f.example/f.xml", _SCRAPED, _DEEP_TRACK_ID, fetch=lambda url: feed)
        assert override == {"episodeUrl": "https://cdn-b.example.com/shows/deep.mp3?feed-token=2"}

    def test_matches_by_exact_title(self):
        feed = _feed_xml(
            [("Deep Episode", "Wed, 04 Dec 2024 17:30:00 GMT", "https://cdn-b.example.com/other-path.mp3")]
        )
        override = _apple_rss_cross_match("https://f.example/f.xml", _SCRAPED, _DEEP_TRACK_ID, fetch=lambda url: feed)
        assert override == {"episodeUrl": "https://cdn-b.example.com/other-path.mp3"}

    def test_matches_by_pub_date_and_casefolded_title(self):
        feed = _feed_xml([("DEEP EPISODE", "Wed, 04 Dec 2024 20:00:00 GMT", "https://cdn-b.example.com/case.mp3")])
        override = _apple_rss_cross_match("https://f.example/f.xml", _SCRAPED, _DEEP_TRACK_ID, fetch=lambda url: feed)
        assert override == {"episodeUrl": "https://cdn-b.example.com/case.mp3"}

    def test_no_match_returns_none(self):
        feed = _feed_xml([("Unrelated", "Mon, 01 Jan 2024 00:00:00 GMT", "https://cdn-b.example.com/x.mp3")])
        assert (
            _apple_rss_cross_match("https://f.example/f.xml", _SCRAPED, _DEEP_TRACK_ID, fetch=lambda url: feed) is None
        )

    def test_fetch_error_returns_none_never_raises(self):
        def boom(url):
            raise ResolverError("Show RSS feed returned HTTP 503")

        assert _apple_rss_cross_match("https://f.example/f.xml", _SCRAPED, _DEEP_TRACK_ID, fetch=boom) is None


# ============================================================================
# Lookup chain — tier ordering (spec #65)
# ============================================================================


def _forbid_page_fetch(monkeypatch):
    def _fail(url):
        raise AssertionError("page fetch must not run when an iTunes tier resolves")

    monkeypatch.setattr(import_service, "_fetch_apple_episode_page", _fail)


class TestDefaultAppleLookupChain:
    def test_tier1_hit_skips_page_scrape(self, monkeypatch):
        episode = {"wrapperType": "podcastEpisode", "trackId": 42}
        monkeypatch.setattr(import_service, "_itunes_lookup", lambda params, *, label: [episode])
        _forbid_page_fetch(monkeypatch)

        assert _default_apple_episode_lookup("42", "7", "https://page.example") == episode

    def test_tier2_hit_skips_page_scrape(self, monkeypatch):
        episode = {"wrapperType": "podcastEpisode", "trackId": 42}

        def fake_lookup(params, *, label):
            if params.startswith("id=42&"):
                return []
            return [{"wrapperType": "track", "kind": "podcast"}, episode]

        monkeypatch.setattr(import_service, "_itunes_lookup", fake_lookup)
        _forbid_page_fetch(monkeypatch)

        assert _default_apple_episode_lookup("42", "7", "https://page.example") == episode

    def test_tier3_resolves_with_show_merge_and_cross_match(self, monkeypatch):
        show_record = {
            "wrapperType": "track",
            "kind": "podcast",
            "feedUrl": "https://feeds.example.com/20vc",
            "collectionName": "The Twenty Minute VC",
            "collectionId": 958230465,
        }

        def fake_lookup(params, *, label):
            if params.startswith(f"id={_DEEP_TRACK_ID}&"):
                return []
            return [show_record]  # window holds newer episodes only

        monkeypatch.setattr(import_service, "_itunes_lookup", fake_lookup)
        monkeypatch.setattr(import_service, "_fetch_apple_episode_page", lambda url: _page_html())
        feed = _feed_xml(
            [
                (
                    "20VC: Revolut Founder Nik Storonsky on When and Where Revolut Will IPO"
                    " & What Revolut Need to Do to Hit $100BN Valuation",
                    "Wed, 04 Dec 2024 17:30:00 GMT",
                    "https://traffic.libsyn.com/secure/thetwentyminutevc/New_Ads_Nik.mp3?dest-id=240976",
                )
            ]
        )
        monkeypatch.setattr(import_service, "_fetch_apple_feed", lambda url: feed)

        info = _default_apple_episode_lookup(_DEEP_TRACK_ID, "958230465", _DEEP_URL)

        assert info["trackName"].startswith("20VC: Revolut Founder Nik Storonsky")
        assert info["feedUrl"] == "https://feeds.example.com/20vc"
        assert info["collectionName"] == "The Twenty Minute VC"
        # Cross-match confirmed the enclosure from the feed (same path).
        assert (
            info["episodeUrl"] == "https://traffic.libsyn.com/secure/thetwentyminutevc/New_Ads_Nik.mp3?dest-id=240976"
        )

    def test_tier2_lookup_error_does_not_block_tier3(self, monkeypatch):
        def fake_lookup(params, *, label):
            if params.startswith(f"id={_DEEP_TRACK_ID}&"):
                return []
            raise ResolverError("iTunes lookup returned HTTP 503")

        monkeypatch.setattr(import_service, "_itunes_lookup", fake_lookup)
        monkeypatch.setattr(import_service, "_fetch_apple_episode_page", lambda url: _page_html())

        info = _default_apple_episode_lookup(_DEEP_TRACK_ID, "958230465", _DEEP_URL)
        # No show record → no feedUrl → parent bootstrap degrades to the
        # synthetic parent downstream; the episode itself still resolves.
        assert info["episodeUrl"].startswith("https://traffic.libsyn.com/")
        assert "feedUrl" not in info

    def test_missing_page_url_keeps_legacy_error(self, monkeypatch):
        monkeypatch.setattr(import_service, "_itunes_lookup", lambda params, *, label: [])
        with pytest.raises(ResolverError) as exc_info:
            _default_apple_episode_lookup("42", "7", None)
        assert "searched 0 entries" in str(exc_info.value)
