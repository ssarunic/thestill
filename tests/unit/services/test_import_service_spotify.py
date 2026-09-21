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

"""End-to-end ImportService tests for Spotify episode imports (spec #79).

The Spotify resolver emits a ``CanonicalParent`` built from the Apple
directory match, so the import flow upserts the show as an ``auto_added``
podcast row exactly as Apple imports do; the canonical id stays
``spotify:<id>`` so re-pasting the same Spotify link dedups.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.core.spotify_resolver import (
    AppleShowMatch,
    EpisodeCandidate,
    EpisodeScore,
    ResolvedSpotifyEpisode,
    SpotifyEpisodeMetadata,
    SpotifyResolutionError,
)
from thestill.models.user import User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.import_service import (
    ImportService,
    ResolverError,
    SpotifyResolver,
    UnsupportedUrlError,
)

_EPISODE_ID = "7kQ2xN9pZ1aB3cD4eF5gH6"
_SPOTIFY_URL = f"https://open.spotify.com/episode/{_EPISODE_ID}?si=share-token"
_FEED_URL = "https://feeds.example.com/sources"


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "imports_spotify.db")
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


def _resolved() -> ResolvedSpotifyEpisode:
    return ResolvedSpotifyEpisode(
        episode_id=_EPISODE_ID,
        spotify=SpotifyEpisodeMetadata(
            episode_id=_EPISODE_ID,
            title="Mark Zuckerberg on Muse, Meta's biggest AI bet yet",
            show_name="Sources with Alex Heath",
            publisher="Alex Heath",
            description="spotify blurb",
            release_date=datetime(2026, 9, 8, 22, 2, tzinfo=timezone.utc),
            duration_seconds=4210,
            image_url="https://i.scdn.co/image/spotify.jpg",
        ),
        show=AppleShowMatch(
            collection_id="1800000001",
            name="Sources with Alex Heath",
            feed_url=_FEED_URL,
            publisher="Alex Heath",
            image_url="https://art.example.com/sources-600.jpg",
            score=1.0,
        ),
        episode=EpisodeCandidate(
            title="Mark Zuckerberg on Muse, Meta's biggest AI bet yet",
            audio_url="https://cdn.example.com/sources/muse.mp3",
            pub_date=datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc),
            duration_seconds=4750,
            external_id="1000700000001",
            description="feed notes",
            image_url="https://art.example.com/muse-600.jpg",
            origin="itunes",
        ),
        score=EpisodeScore(title=1.0, date=1.0, duration=0.6, combined=0.94, accepted=True),
    )


class _StubLinkResolver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.urls = []

    def resolve_episode(self, url):
        self.urls.append(url)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _service_with(repo, inbox_repo, queue, outcome, **kwargs):
    return ImportService(
        repository=repo,
        inbox_repository=inbox_repo,
        queue_manager=queue,
        resolvers=[SpotifyResolver(link_resolver=_StubLinkResolver(outcome))],
        **kwargs,
    )


def _make_user(user_repo, email):
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


class TestSpotifyResolverUnit:
    def test_matches_spotify_shapes_only(self):
        resolver = SpotifyResolver(link_resolver=_StubLinkResolver(_resolved()))
        assert resolver.matches(_SPOTIFY_URL)
        assert resolver.matches("https://spotify.link/AbC123")
        assert resolver.matches(f"spotify:episode:{_EPISODE_ID}")
        assert not resolver.matches("https://podcasts.apple.com/us/podcast/x/id1?i=2")
        assert not resolver.matches("https://notspotify.com/episode/x")

    def test_canonical_source_shape(self):
        source = SpotifyResolver(link_resolver=_StubLinkResolver(_resolved())).resolve(_SPOTIFY_URL)
        assert source.kind == "spotify_episode"
        assert source.canonical_id == f"spotify:{_EPISODE_ID}"
        assert source.external_id == _EPISODE_ID
        # Audio + metadata come from the feed-side match (what refresh will see).
        assert source.audio_url == "https://cdn.example.com/sources/muse.mp3"
        assert source.duration_seconds == 4750
        assert source.description == "feed notes"
        assert source.image_url == "https://art.example.com/muse-600.jpg"
        assert source.source_handle == "Sources with Alex Heath"
        assert source.parent is not None
        assert source.parent.rss_url == _FEED_URL
        assert source.parent.external_id == "1800000001"
        assert source.parent.title == "Sources with Alex Heath"
        assert source.parent.image_url == "https://art.example.com/sources-600.jpg"

    def test_falls_back_to_spotify_metadata_when_feed_side_is_thin(self):
        resolved = _resolved()
        thin = ResolvedSpotifyEpisode(
            episode_id=resolved.episode_id,
            spotify=resolved.spotify,
            show=resolved.show,
            episode=EpisodeCandidate(title="", audio_url="https://cdn/x.mp3"),
            score=resolved.score,
        )
        source = SpotifyResolver(link_resolver=_StubLinkResolver(thin)).resolve(_SPOTIFY_URL)
        assert source.title == "Mark Zuckerberg on Muse, Meta's biggest AI bet yet"
        assert source.description == "spotify blurb"
        assert source.duration_seconds == 4210
        assert source.pub_date == datetime(2026, 9, 8, 22, 2, tzinfo=timezone.utc)
        assert source.image_url == "https://art.example.com/sources-600.jpg"

    def test_resolution_errors_become_resolver_errors_verbatim(self):
        resolver = SpotifyResolver(link_resolver=_StubLinkResolver(SpotifyResolutionError("Could not find “X”.")))
        with pytest.raises(ResolverError, match="Could not find “X”."):
            resolver.resolve(_SPOTIFY_URL)


def test_spotify_import_upserts_show_as_auto_added(repo, inbox_repo, queue, user_repo, db_path):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, _resolved())

    result = svc.import_url(user_id=alice.id, url=_SPOTIFY_URL)

    assert result.episode_created
    assert result.canonical_id == f"spotify:{_EPISODE_ID}"
    assert result.kind == "spotify_episode"
    assert result.source_handle == "Sources with Alex Heath"
    assert result.parent_title == "Sources with Alex Heath"
    assert result.parent_slug == "sources-with-alex-heath"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        podcast = conn.execute(
            "SELECT id, title, synthetic, auto_added FROM podcasts WHERE rss_url = ?", (_FEED_URL,)
        ).fetchone()
        assert podcast is not None
        assert podcast["auto_added"] == 1
        assert podcast["synthetic"] == 0
        episode = conn.execute(
            "SELECT podcast_id, audio_url, title, canonical_id, external_id FROM episodes WHERE id = ?",
            (result.episode_id,),
        ).fetchone()
        assert episode["podcast_id"] == podcast["id"]
        assert episode["audio_url"] == "https://cdn.example.com/sources/muse.mp3"
        assert episode["canonical_id"] == f"spotify:{_EPISODE_ID}"
        assert episode["external_id"] == _EPISODE_ID

    assert queue.get_pending_count() == 1
    tasks = queue.get_tasks_for_episode(result.episode_id)
    assert [t.stage for t in tasks] == [TaskStage.TRANSCRIBE]


def test_spotify_import_dedups_by_canonical_id(repo, inbox_repo, queue, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    svc = _service_with(repo, inbox_repo, queue, _resolved())

    first = svc.import_url(user_id=alice.id, url=_SPOTIFY_URL)
    again = svc.import_url(user_id=alice.id, url=f"https://open.spotify.com/intl-de/episode/{_EPISODE_ID}")
    other_user = svc.import_url(user_id=bob.id, url=f"spotify:episode:{_EPISODE_ID}")

    assert again.episode_id == first.episode_id
    assert not again.episode_created and not again.inbox_created
    assert other_user.episode_id == first.episode_id
    assert not other_user.episode_created and other_user.inbox_created
    assert queue.get_pending_count() == 1


def test_spotify_import_attaches_to_feed_episode_when_feed_manager_injected(
    repo, inbox_repo, queue, user_repo, db_path
):
    """With a feed manager, the parent's RSS is ingested and the import binds to the feed's row by audio_url."""
    alice = _make_user(user_repo, "alice@example.com")

    feed_row_id = str(uuid.uuid4())

    class FakeFeedManager:
        def get_new_episodes(self, *, podcast_id):
            now = datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO episodes (id, podcast_id, created_at, updated_at,
                                          external_id, title, slug, description,
                                          description_html, audio_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?)
                    """,
                    (
                        feed_row_id,
                        podcast_id,
                        now,
                        now,
                        "feed-guid-1",
                        "Mark Zuckerberg on Muse, Meta's biggest AI bet yet",
                        "mark-zuckerberg-on-muse",
                        "https://cdn.example.com/sources/muse.mp3",
                    ),
                )
                conn.commit()
            return []

    svc = _service_with(repo, inbox_repo, queue, _resolved(), feed_manager=FakeFeedManager())
    result = svc.import_url(user_id=alice.id, url=_SPOTIFY_URL)

    assert result.episode_id == feed_row_id
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT canonical_id FROM episodes WHERE id = ?", (feed_row_id,)).fetchone()
        assert row[0] == f"spotify:{_EPISODE_ID}"


def test_spotify_resolution_failure_surfaces_user_message(repo, inbox_repo, queue, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(
        repo,
        inbox_repo,
        queue,
        SpotifyResolutionError("Could not find “Exclusive” in the Apple Podcasts directory."),
    )
    with pytest.raises(ResolverError, match="Apple Podcasts directory"):
        svc.import_url(user_id=alice.id, url=_SPOTIFY_URL)
    assert queue.get_pending_count() == 0


def test_default_resolver_lineup_includes_spotify(repo, inbox_repo, queue):
    svc = ImportService(repository=repo, inbox_repository=inbox_repo, queue_manager=queue)
    names = [type(r).__name__ for r in svc._resolvers]  # pylint: disable=protected-access
    assert names == ["ApplePodcastsResolver", "YouTubeResolver", "SpotifyResolver", "BareAudioResolver"]


def test_unsupported_url_message_lists_spotify(repo, inbox_repo, queue, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    svc = ImportService(repository=repo, inbox_repository=inbox_repo, queue_manager=queue)
    with pytest.raises(UnsupportedUrlError, match="Spotify episode links"):
        svc.import_url(user_id=alice.id, url="https://vimeo.com/12345")
