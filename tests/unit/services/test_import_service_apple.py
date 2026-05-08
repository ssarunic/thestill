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

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.core.queue_manager import QueueManager
from thestill.models.user import User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.import_service import ApplePodcastsResolver, ImportService

_APPLE_URL = "https://podcasts.apple.com/us/podcast/the-daily/id1200361736?i=1000620312000"


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
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda track_id, collection_id=None: info)],
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
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None: fake_apple_episode_info)],
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
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None: fake_apple_episode_info)],
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
        resolvers=[ApplePodcastsResolver(episode_lookup=lambda tid, cid=None: fake_apple_episode_info)],
        feed_manager=BrokenFeedManager(),
    )

    result = svc.import_url(user_id=alice.id, url=_APPLE_URL)
    assert result.episode_created
    assert result.canonical_id == "apple:1000620312000"
