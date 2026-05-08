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

"""Unit tests for ``SqliteInboxRepository`` (spec #29)."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.inbox import InboxEntry
from thestill.models.podcast import Episode, Podcast
from thestill.models.user import PodcastFollower, User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_follower_repository import (
    SqlitePodcastFollowerRepository,
)
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository


@pytest.fixture
def db_path(tmp_path):
    """Initialize all schemas (podcasts, users, followers, inbox)."""
    path = tmp_path / "inbox_test.db"
    SqlitePodcastRepository(str(path))  # creates schema + runs migrations
    return str(path)


@pytest.fixture
def podcast_repo(db_path):
    return SqlitePodcastRepository(db_path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def follower_repo(db_path):
    return SqlitePodcastFollowerRepository(db_path)


@pytest.fixture
def inbox_repo(db_path):
    return SqliteInboxRepository(db_path)


def _make_user(user_repo, email: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def _make_podcast(podcast_repo, *, slug: str, title: str = "Test") -> Podcast:
    podcast = Podcast(
        id=str(uuid.uuid4()),
        title=title,
        slug=slug,
        rss_url=f"https://example.com/{slug}.xml",
        description="A test podcast",
        image_url="https://cdn.example.com/cover.jpg",
    )
    podcast_repo.save(podcast)
    return podcast


def _make_episode(
    podcast_repo,
    *,
    podcast_id: str,
    title: str,
    published_at: datetime | None = None,
) -> Episode:
    """Insert an episode and optionally set its ``published_at``."""
    episode = Episode(
        id=str(uuid.uuid4()),
        podcast_id=podcast_id,
        external_id=f"ext-{title}",
        title=title,
        description="Episode description",
        audio_url=f"https://cdn.example.com/{title}.mp3",
    )

    with sqlite3.connect(podcast_repo.db_path) as conn:
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url, published_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                episode.id,
                podcast_id,
                episode.external_id,
                episode.title,
                episode.slug,
                episode.description,
                str(episode.audio_url),
                published_at.isoformat() if published_at else None,
            ),
        )
        conn.commit()

    if published_at is not None:
        episode.published_at = published_at
    return episode


def _follow(follower_repo, user_id: str, podcast_id: str) -> PodcastFollower:
    f = PodcastFollower(user_id=user_id, podcast_id=podcast_id)
    follower_repo.add(f)
    return f


# ============================================================================
# insert_many + get
# ============================================================================


def test_insert_many_inserts_rows_and_returns_count(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")

    entries = [
        InboxEntry(user_id=user.id, episode_id=ep.id, source="follow_new"),
    ]
    assert inbox_repo.insert_many(entries) == 1

    fetched = inbox_repo.get(user.id, ep.id)
    assert fetched is not None
    assert fetched.source == "follow_new"
    assert fetched.state == "unread"
    assert fetched.state_changed_at is None


def test_insert_many_is_idempotent_on_user_episode_pair(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")

    inbox_repo.insert_many([InboxEntry(user_id=user.id, episode_id=ep.id, source="follow_new")])
    # Re-insert the same pair (different uuid); should be a no-op.
    second = inbox_repo.insert_many([InboxEntry(user_id=user.id, episode_id=ep.id, source="follow_seed")])
    assert second == 0

    # The original row's source is preserved.
    entry = inbox_repo.get(user.id, ep.id)
    assert entry is not None
    assert entry.source == "follow_new"


def test_insert_many_empty_list_returns_zero(inbox_repo):
    assert inbox_repo.insert_many([]) == 0


def test_insert_many_rejects_invalid_source(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")

    with pytest.raises(ValueError):
        bogus = InboxEntry.model_construct(
            id=str(uuid.uuid4()),
            user_id=user.id,
            episode_id=ep.id,
            source="bogus",  # bypass Pydantic literal validation
            state="unread",
            delivered_at=datetime.now(timezone.utc),
            state_changed_at=None,
        )
        inbox_repo.insert_many([bogus])


# ============================================================================
# update_state
# ============================================================================


def test_update_state_sets_state_and_changed_at(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")
    inbox_repo.insert_many([InboxEntry(user_id=user.id, episode_id=ep.id, source="follow_new")])

    now = datetime.now(timezone.utc)
    entry = inbox_repo.update_state(user.id, ep.id, "read", now)

    assert entry is not None
    assert entry.state == "read"
    assert entry.state_changed_at is not None
    # ISO round-trip preserves the timestamp.
    assert entry.state_changed_at.isoformat() == now.isoformat()


def test_update_state_returns_none_when_no_row(inbox_repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    result = inbox_repo.update_state(user.id, "missing-episode-id", "read", datetime.now(timezone.utc))
    assert result is None


def test_update_state_rejects_invalid_state(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")
    inbox_repo.insert_many([InboxEntry(user_id=user.id, episode_id=ep.id, source="follow_new")])

    with pytest.raises(ValueError):
        inbox_repo.update_state(user.id, ep.id, "archived", datetime.now(timezone.utc))


# ============================================================================
# list_items
# ============================================================================


def test_list_items_orders_newest_first_and_excludes_dismissed_by_default(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep1 = _make_episode(podcast_repo, podcast_id=podcast.id, title="older")
    ep2 = _make_episode(podcast_repo, podcast_id=podcast.id, title="newer")
    ep3 = _make_episode(podcast_repo, podcast_id=podcast.id, title="dismissed")

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inbox_repo.insert_many(
        [
            InboxEntry(
                user_id=user.id,
                episode_id=ep1.id,
                source="follow_new",
                delivered_at=base,
            ),
            InboxEntry(
                user_id=user.id,
                episode_id=ep2.id,
                source="follow_new",
                delivered_at=base + timedelta(hours=1),
            ),
            InboxEntry(
                user_id=user.id,
                episode_id=ep3.id,
                source="follow_new",
                delivered_at=base + timedelta(hours=2),
            ),
        ]
    )
    inbox_repo.update_state(user.id, ep3.id, "dismissed", datetime.now(timezone.utc))

    items = inbox_repo.list_items(user.id)
    titles = [item.episode.title for item in items]
    assert titles == ["newer", "older"]
    # Composed view exposes podcast metadata too.
    assert items[0].podcast.slug == "p1"


def test_list_items_state_filter(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep1 = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")
    ep2 = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep2")
    inbox_repo.insert_many(
        [
            InboxEntry(user_id=user.id, episode_id=ep1.id, source="follow_new"),
            InboxEntry(user_id=user.id, episode_id=ep2.id, source="follow_new"),
        ]
    )
    inbox_repo.update_state(user.id, ep2.id, "saved", datetime.now(timezone.utc))

    saved = inbox_repo.list_items(user.id, state="saved")
    assert [i.episode.title for i in saved] == ["ep2"]

    unread = inbox_repo.list_items(user.id, state="unread")
    assert [i.episode.title for i in unread] == ["ep1"]


def test_list_items_before_cursor(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_old = _make_episode(podcast_repo, podcast_id=podcast.id, title="old")
    ep_new = _make_episode(podcast_repo, podcast_id=podcast.id, title="new")

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    inbox_repo.insert_many(
        [
            InboxEntry(
                user_id=user.id,
                episode_id=ep_old.id,
                source="follow_new",
                delivered_at=base,
            ),
            InboxEntry(
                user_id=user.id,
                episode_id=ep_new.id,
                source="follow_new",
                delivered_at=base + timedelta(hours=1),
            ),
        ]
    )

    older_only = inbox_repo.list_items(user.id, before=base + timedelta(hours=1))
    assert [i.episode.title for i in older_only] == ["old"]


def test_list_items_limit_zero_returns_empty(inbox_repo, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert inbox_repo.list_items(user.id, limit=0) == []


def test_list_items_does_not_leak_other_users_rows(inbox_repo, user_repo, podcast_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep = _make_episode(podcast_repo, podcast_id=podcast.id, title="shared")

    inbox_repo.insert_many(
        [
            InboxEntry(user_id=alice.id, episode_id=ep.id, source="follow_new"),
            InboxEntry(user_id=bob.id, episode_id=ep.id, source="follow_new"),
        ]
    )

    assert len(inbox_repo.list_items(alice.id)) == 1
    assert len(inbox_repo.list_items(bob.id)) == 1
    inbox_repo.update_state(alice.id, ep.id, "read", datetime.now(timezone.utc))
    # bob still sees it as unread.
    bob_unread = inbox_repo.list_items(bob.id, state="unread")
    assert len(bob_unread) == 1


# ============================================================================
# unread_count
# ============================================================================


def test_unread_count_only_counts_unread(inbox_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep1 = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep1")
    ep2 = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep2")
    ep3 = _make_episode(podcast_repo, podcast_id=podcast.id, title="ep3")

    inbox_repo.insert_many(
        [
            InboxEntry(user_id=user.id, episode_id=ep1.id, source="follow_new"),
            InboxEntry(user_id=user.id, episode_id=ep2.id, source="follow_new"),
            InboxEntry(user_id=user.id, episode_id=ep3.id, source="follow_new"),
        ]
    )
    now = datetime.now(timezone.utc)
    inbox_repo.update_state(user.id, ep2.id, "read", now)
    inbox_repo.update_state(user.id, ep3.id, "saved", now)

    assert inbox_repo.unread_count(user.id) == 1


# ============================================================================
# followers_of_podcast + recent_published_episode_ids
# ============================================================================


def test_followers_of_podcast(inbox_repo, user_repo, podcast_repo, follower_repo):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    _make_user(user_repo, "carol@example.com")  # not following
    podcast = _make_podcast(podcast_repo, slug="p1")
    other = _make_podcast(podcast_repo, slug="p2")

    _follow(follower_repo, alice.id, podcast.id)
    _follow(follower_repo, bob.id, podcast.id)
    # Following a different podcast should not bleed in.
    _follow(follower_repo, alice.id, other.id)

    followers = set(inbox_repo.followers_of_podcast(podcast.id))
    assert followers == {alice.id, bob.id}


def test_recent_published_episode_ids_orders_by_published_at_desc(inbox_repo, podcast_repo):
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    older = _make_episode(podcast_repo, podcast_id=podcast.id, title="older", published_at=base)
    newer = _make_episode(
        podcast_repo,
        podcast_id=podcast.id,
        title="newer",
        published_at=base + timedelta(days=1),
    )
    _make_episode(podcast_repo, podcast_id=podcast.id, title="unpublished", published_at=None)

    ids = inbox_repo.recent_published_episode_ids(podcast.id, limit=10)
    assert ids == [newer.id, older.id]


def test_recent_published_episode_ids_respects_limit(inbox_repo, podcast_repo):
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        _make_episode(
            podcast_repo,
            podcast_id=podcast.id,
            title=f"ep-{i}",
            published_at=base + timedelta(days=i),
        )
    ids = inbox_repo.recent_published_episode_ids(podcast.id, limit=2)
    assert len(ids) == 2


def test_recent_published_episode_ids_excludes_unpublished(inbox_repo, podcast_repo):
    podcast = _make_podcast(podcast_repo, slug="p1")
    _make_episode(podcast_repo, podcast_id=podcast.id, title="never", published_at=None)
    ids = inbox_repo.recent_published_episode_ids(podcast.id, limit=10)
    assert ids == []
