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

"""Unit tests for ``InboxService``."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.inbox import InboxEntry
from thestill.models.podcast import Podcast
from thestill.models.user import PodcastFollower, User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.inbox_service import InboxEntryNotFoundError, InboxService, InvalidInboxStateError


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "inbox_service.db"
    SqlitePodcastRepository(str(path))
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


@pytest.fixture
def service(inbox_repo, follower_repo):
    return InboxService(inbox_repo, follower_repo, seed_on_follow_count=2)


def _make_user(user_repo, email: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def _make_podcast(podcast_repo, *, slug: str) -> Podcast:
    podcast = Podcast(
        id=str(uuid.uuid4()),
        title=f"Podcast {slug}",
        slug=slug,
        rss_url=f"https://example.com/{slug}.xml",
        description="d",
    )
    podcast_repo.save(podcast)
    return podcast


def _make_published_episode(db_path, podcast_id, title, published_at, *, pub_date=None):
    episode_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url, published_at, pub_date
            ) VALUES (?, ?, ?, ?, '', '', '', ?, ?, ?)
            """,
            (
                episode_id,
                podcast_id,
                f"ext-{title}",
                title,
                f"https://cdn.example.com/{title}.mp3",
                published_at.isoformat() if published_at else None,
                pub_date.isoformat() if pub_date else None,
            ),
        )
        conn.commit()
    return episode_id


# ============================================================================
# fanout_on_publish
# ============================================================================


def test_fanout_on_publish_inserts_one_row_per_follower(
    service, db_path, user_repo, podcast_repo, follower_repo, inbox_repo
):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    follower_repo.add(PodcastFollower(user_id=alice.id, podcast_id=podcast.id))
    follower_repo.add(PodcastFollower(user_id=bob.id, podcast_id=podcast.id))

    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))

    inserted = service.fanout_on_publish(ep_id, podcast.id)
    assert inserted == 2
    assert inbox_repo.get(alice.id, ep_id) is not None
    assert inbox_repo.get(bob.id, ep_id) is not None
    # Source for fan-out is always ``follow_new``.
    assert inbox_repo.get(alice.id, ep_id).source == "follow_new"


def test_fanout_on_publish_is_idempotent_on_repeat(service, db_path, user_repo, podcast_repo, follower_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    follower_repo.add(PodcastFollower(user_id=alice.id, podcast_id=podcast.id))

    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))

    assert service.fanout_on_publish(ep_id, podcast.id) == 1
    # Second call: row already exists for (alice, ep_id) → 0 inserts.
    assert service.fanout_on_publish(ep_id, podcast.id) == 0


def test_fanout_on_publish_no_followers_returns_zero(service, db_path, podcast_repo):
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))
    assert service.fanout_on_publish(ep_id, podcast.id) == 0


# ============================================================================
# seed_on_follow
# ============================================================================


def test_seed_on_follow_delivers_recent_published_episodes(service, db_path, user_repo, podcast_repo, inbox_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _make_published_episode(db_path, podcast.id, "old", base)
    mid_id = _make_published_episode(db_path, podcast.id, "mid", base + timedelta(days=1))
    new_id = _make_published_episode(db_path, podcast.id, "new", base + timedelta(days=2))

    inserted = service.seed_on_follow(alice.id, podcast.id)
    # ``seed_on_follow_count = 2`` → newest 2 only.
    assert inserted == 2

    items = inbox_repo.list_items(alice.id)
    delivered_episode_ids = {item.entry.episode_id for item in items}
    assert delivered_episode_ids == {mid_id, new_id}
    for item in items:
        assert item.entry.source == "follow_seed"


def test_seed_on_follow_no_published_episodes_returns_zero(service, db_path, user_repo, podcast_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    # Episode exists but is unpublished.
    _make_published_episode(db_path, podcast.id, "draft", None)
    assert service.seed_on_follow(alice.id, podcast.id) == 0


def test_seed_on_follow_zero_count_short_circuits(inbox_repo, follower_repo, user_repo, podcast_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    service = InboxService(inbox_repo, follower_repo, seed_on_follow_count=0)
    assert service.seed_on_follow(user.id, podcast.id) == 0


def test_seed_on_follow_orders_delivered_at_so_newest_lands_on_top(
    service, db_path, user_repo, podcast_repo, inbox_repo
):
    """The most-recently-aired seeded episode must have the largest delivered_at.

    Within one follow event we want the inbox view (sorted by
    delivered_at DESC) to show the newest episode at the top, then older
    episodes beneath it — matching the way a listener thinks of "I just
    subscribed; here's what's new".
    """
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    air_base = datetime(2026, 4, 1, 8, 15, tzinfo=timezone.utc)
    pipeline_base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    # Aired Apr 1 / Apr 2 / Apr 3, but pipeline finished them out of
    # order — older episodes were processed *after* newer ones.
    old_id = _make_published_episode(
        db_path,
        podcast.id,
        "old",
        published_at=pipeline_base + timedelta(hours=2),
        pub_date=air_base,
    )
    mid_id = _make_published_episode(
        db_path,
        podcast.id,
        "mid",
        published_at=pipeline_base + timedelta(hours=0),
        pub_date=air_base + timedelta(days=1),
    )
    new_id = _make_published_episode(
        db_path,
        podcast.id,
        "new",
        published_at=pipeline_base + timedelta(hours=1),
        pub_date=air_base + timedelta(days=2),
    )

    inserted = service.seed_on_follow(alice.id, podcast.id)
    assert inserted == 2

    items = inbox_repo.list_items(alice.id)
    # Selection: pub_date drives the pick (newest 2 by air date), so
    # ``old`` is excluded even though its pipeline timestamp was the
    # latest.
    delivered_ids = [item.entry.episode_id for item in items]
    assert old_id not in delivered_ids
    # Order: list_items returns delivered_at DESC. The newest-aired
    # episode (new_id) lands first, then mid_id.
    assert delivered_ids == [new_id, mid_id]
    assert items[0].entry.delivered_at > items[1].entry.delivered_at


def test_seed_on_follow_skips_already_delivered(service, db_path, user_repo, podcast_repo, follower_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", base)
    follower_repo.add(PodcastFollower(user_id=alice.id, podcast_id=podcast.id))

    # Pretend fan-out already delivered ep1 to alice.
    service.fanout_on_publish(ep_id, podcast.id)

    # seed_on_follow tries to deliver the same ep1; OR IGNORE makes it 0.
    assert service.seed_on_follow(alice.id, podcast.id) == 0


# ============================================================================
# mark_state
# ============================================================================


def test_mark_state_updates_existing_row(service, db_path, user_repo, podcast_repo, inbox_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))
    inbox_repo.insert_many([InboxEntry(user_id=alice.id, episode_id=ep_id, source="follow_new")])

    entry = service.mark_state(alice.id, ep_id, "saved")
    assert entry.state == "saved"
    assert entry.state_changed_at is not None


def test_mark_state_invalid_state_raises(service, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    with pytest.raises(InvalidInboxStateError):
        service.mark_state(alice.id, "any-episode", "archived")


def test_mark_state_missing_row_raises(service, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    with pytest.raises(InboxEntryNotFoundError):
        service.mark_state(alice.id, "nonexistent-episode", "read")


# ============================================================================
# list + unread_count
# ============================================================================


def test_list_excludes_dismissed_by_default(service, db_path, user_repo, podcast_repo, inbox_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_keep_id = _make_published_episode(db_path, podcast.id, "keep", datetime.now(timezone.utc))
    ep_dismiss_id = _make_published_episode(db_path, podcast.id, "dismiss", datetime.now(timezone.utc))
    inbox_repo.insert_many(
        [
            InboxEntry(user_id=alice.id, episode_id=ep_keep_id, source="follow_new"),
            InboxEntry(user_id=alice.id, episode_id=ep_dismiss_id, source="follow_new"),
        ]
    )
    service.mark_state(alice.id, ep_dismiss_id, "dismissed")

    items = service.list(alice.id)
    titles = [i.episode.title for i in items]
    assert titles == ["keep"]


def test_list_invalid_state_raises(service, user_repo):
    alice = _make_user(user_repo, "alice@example.com")
    with pytest.raises(InvalidInboxStateError):
        service.list(alice.id, state="archived")


def test_unread_count(service, db_path, user_repo, podcast_repo, inbox_repo):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep1 = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))
    ep2 = _make_published_episode(db_path, podcast.id, "ep2", datetime.now(timezone.utc))
    inbox_repo.insert_many(
        [
            InboxEntry(user_id=alice.id, episode_id=ep1, source="follow_new"),
            InboxEntry(user_id=alice.id, episode_id=ep2, source="follow_new"),
        ]
    )
    service.mark_state(alice.id, ep1, "read")

    assert service.unread_count(alice.id) == 1


def test_service_rejects_negative_seed_count(inbox_repo, follower_repo):
    with pytest.raises(ValueError):
        InboxService(inbox_repo, follower_repo, seed_on_follow_count=-1)


# ============================================================================
# auto-transcribe on delivery (any inbox entry → full pipeline)
# ============================================================================


def _mark_episode_artifact(db_path, episode_id, column, value):
    """Stamp an artifact path so the episode no longer looks DISCOVERED."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE episodes SET {column} = ? WHERE id = ?", (value, episode_id))
        conn.commit()


@pytest.fixture
def queue_manager(db_path):
    from thestill.core.queue_manager import QueueManager

    return QueueManager(db_path)


@pytest.fixture
def transcribing_service(inbox_repo, follower_repo, podcast_repo, queue_manager):
    """InboxService wired with the auto-transcribe plumbing (Dalston URL path)."""
    return InboxService(
        inbox_repo,
        follower_repo,
        seed_on_follow_count=2,
        queue_manager=queue_manager,
        podcast_repository=podcast_repo,
        transcription_provider="dalston",
    )


def _stages_for(queue_manager, episode_id):
    return [t.stage for t in queue_manager.get_tasks_for_episode(episode_id)]


def test_seed_on_follow_submits_backlog_for_pipeline(
    transcribing_service, db_path, user_repo, podcast_repo, queue_manager
):
    from thestill.core.queue_manager import TaskStage

    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _make_published_episode(db_path, podcast.id, "old", base)
    mid_id = _make_published_episode(db_path, podcast.id, "mid", base + timedelta(days=1))
    new_id = _make_published_episode(db_path, podcast.id, "new", base + timedelta(days=2))

    assert transcribing_service.seed_on_follow(alice.id, podcast.id) == 2

    # The two seeded episodes are enqueued; Dalston + audio_url → TRANSCRIBE
    # (download/downsample skipped). run_full_pipeline keeps the chain going.
    for ep_id in (mid_id, new_id):
        tasks = queue_manager.get_tasks_for_episode(ep_id)
        assert [t.stage for t in tasks] == [TaskStage.TRANSCRIBE]
        assert tasks[0].metadata.get("run_full_pipeline") is True
        assert tasks[0].metadata.get("initiated_by") == "inbox-follow_seed"


def test_seed_on_follow_transcribes_backlog_when_nothing_published_yet(
    transcribing_service, db_path, user_repo, podcast_repo, queue_manager
):
    """Regression: a brand-new podcast has NO published episodes (publish only
    happens post-pipeline), so inbox delivery is empty on first follow — but the
    recent backlog must still be enqueued for transcription, selected by air
    date (``pub_date``), not publish state."""
    from thestill.core.queue_manager import TaskStage

    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # published_at=None mirrors a freshly-discovered episode; only pub_date set.
    old_id = _make_published_episode(db_path, podcast.id, "old", None, pub_date=base)
    mid_id = _make_published_episode(db_path, podcast.id, "mid", None, pub_date=base + timedelta(days=1))
    new_id = _make_published_episode(db_path, podcast.id, "new", None, pub_date=base + timedelta(days=2))

    # Nothing is published → inbox delivery is 0...
    assert transcribing_service.seed_on_follow(alice.id, podcast.id) == 0
    # ...but the newest 2 (by air date) are enqueued for transcription anyway.
    assert _stages_for(queue_manager, new_id) == [TaskStage.TRANSCRIBE]
    assert _stages_for(queue_manager, mid_id) == [TaskStage.TRANSCRIBE]
    assert _stages_for(queue_manager, old_id) == []  # beyond seed_on_follow_count=2


def test_seed_on_follow_non_dalston_starts_at_download(
    inbox_repo, follower_repo, podcast_repo, queue_manager, db_path, user_repo
):
    from thestill.core.queue_manager import TaskStage

    service = InboxService(
        inbox_repo,
        follower_repo,
        seed_on_follow_count=2,
        queue_manager=queue_manager,
        podcast_repository=podcast_repo,
        transcription_provider="whisper",
    )
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))

    service.seed_on_follow(alice.id, podcast.id)
    assert _stages_for(queue_manager, ep_id) == [TaskStage.DOWNLOAD]


def test_seed_on_follow_skips_already_processed_episode(
    transcribing_service, db_path, user_repo, podcast_repo, queue_manager
):
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "done", datetime.now(timezone.utc))
    # Already transcribed → not an orphan → must not be re-enqueued.
    _mark_episode_artifact(db_path, ep_id, "raw_transcript_path", "raw/done.json")

    transcribing_service.seed_on_follow(alice.id, podcast.id)
    assert queue_manager.get_tasks_for_episode(ep_id) == []


def test_seed_on_follow_enqueue_is_idempotent(transcribing_service, db_path, user_repo, podcast_repo, queue_manager):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))

    transcribing_service.seed_on_follow(alice.id, podcast.id)
    # A second follower seeding the same episode must not double-enqueue: the
    # episode now has a pending task, so the guard coalesces it.
    transcribing_service.seed_on_follow(bob.id, podcast.id)
    assert len(queue_manager.get_tasks_for_episode(ep_id)) == 1


def test_seed_on_follow_without_queue_plumbing_does_not_enqueue(
    service, db_path, user_repo, podcast_repo, queue_manager
):
    # The default ``service`` fixture has no queue_manager → delivery only,
    # no transcription side effect.
    alice = _make_user(user_repo, "alice@example.com")
    podcast = _make_podcast(podcast_repo, slug="p1")
    ep_id = _make_published_episode(db_path, podcast.id, "ep1", datetime.now(timezone.utc))

    assert service.seed_on_follow(alice.id, podcast.id) == 1
    assert queue_manager.get_tasks_for_episode(ep_id) == []
