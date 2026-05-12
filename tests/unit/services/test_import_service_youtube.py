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

"""End-to-end ImportService tests for YouTube imports.

These exercise the auto-added-parent path: a YouTube import upserts the
channel as a real ``podcasts`` row (auto_added=1), the episode points at
that row, refresh skips it until someone follows.
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage
from thestill.models.user import User
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SYNTHETIC_AUDIO_IMPORTS_ID, SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.import_service import ImportService, YouTubeResolver


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "imports_yt.db")
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
        resolvers=[YouTubeResolver(metadata_fetcher=lambda url: info)],
    )


def _make_user(user_repo, email):
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


# ============================================================================
# Auto-added parent
# ============================================================================


def test_youtube_import_upserts_channel_as_auto_added(
    repo, inbox_repo, queue, user_repo, db_path, fake_youtube_video_info
):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_youtube_video_info)

    result = svc.import_url(user_id=alice.id, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert result.episode_created
    assert result.canonical_id == "youtube:dQw4w9WgXcQ"
    assert result.kind == "youtube"

    # Channel row exists, auto_added=1, NOT synthetic.
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rss = "https://www.youtube.com/feeds/videos.xml?channel_id=UCuAXFkgsw1L7xaCfnd5JJOw"
        row = conn.execute(
            "SELECT id, title, slug, synthetic, auto_added FROM podcasts WHERE rss_url = ?",
            (rss,),
        ).fetchone()
        assert row is not None
        assert row["auto_added"] == 1
        assert row["synthetic"] == 0
        assert row["title"] == "Rick Astley"
        assert row["slug"] == "rick-astley"

        # Episode points at the channel, not at the synthetic audio-imports parent.
        ep = conn.execute(
            "SELECT podcast_id, canonical_id FROM episodes WHERE id = ?",
            (result.episode_id,),
        ).fetchone()
        assert ep["podcast_id"] == row["id"]
        assert ep["podcast_id"] != SYNTHETIC_AUDIO_IMPORTS_ID
        assert ep["canonical_id"] == "youtube:dQw4w9WgXcQ"

    # Pipeline kicked off — imports start at TRANSCRIBE (URL mode), no DOWNLOAD.
    task = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
    assert task is not None and task.episode_id == result.episode_id
    assert queue.get_next_task(stage=TaskStage.DOWNLOAD) is None


# ============================================================================
# Refresh predicate behaviour
# ============================================================================


def _follow(db_path, user_id, podcast_id):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, podcast_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def test_auto_added_channel_excluded_from_refresh_until_followed(
    repo, inbox_repo, queue, user_repo, db_path, fake_youtube_video_info
):
    alice = _make_user(user_repo, "alice@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_youtube_video_info)
    svc.import_url(user_id=alice.id, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    rss = "https://www.youtube.com/feeds/videos.xml?channel_id=UCuAXFkgsw1L7xaCfnd5JJOw"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        channel_id = conn.execute("SELECT id FROM podcasts WHERE rss_url = ?", (rss,)).fetchone()["id"]

    # Before any follow: refresh skips both the synthetic parent AND the
    # auto-added channel, so the result is empty.
    assert repo.get_podcasts_for_refresh()[0] == []

    _follow(db_path, alice.id, channel_id)

    # After follow: channel is back in the refresh set.
    podcasts, _ = repo.get_podcasts_for_refresh()
    assert [p.id for p in podcasts] == [channel_id]


# ============================================================================
# Idempotency across users
# ============================================================================


def test_two_users_share_episode_and_channel(repo, inbox_repo, queue, user_repo, db_path, fake_youtube_video_info):
    alice = _make_user(user_repo, "alice@example.com")
    bob = _make_user(user_repo, "bob@example.com")
    svc = _service_with(repo, inbox_repo, queue, fake_youtube_video_info)

    r1 = svc.import_url(user_id=alice.id, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    r2 = svc.import_url(user_id=bob.id, url="https://youtu.be/dQw4w9WgXcQ?si=tracking")

    # Same canonical id → same episode → same channel row.
    assert r1.episode_id == r2.episode_id
    assert r2.episode_created is False
    assert r2.inbox_created is True

    rss = "https://www.youtube.com/feeds/videos.xml?channel_id=UCuAXFkgsw1L7xaCfnd5JJOw"
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM podcasts WHERE rss_url = ?", (rss,)).fetchone()
        assert rows[0] == 1

    # Pipeline runs exactly once across both imports.
    seen = []
    while True:
        t = queue.get_next_task(stage=TaskStage.TRANSCRIBE)
        if t is None:
            break
        seen.append(t)
    assert len(seen) == 1


def test_existing_followed_channel_is_not_overwritten_by_import(
    repo, inbox_repo, queue, user_repo, db_path, fake_youtube_video_info
):
    """If a user already manually subscribed to the channel (auto_added=0,
    a follower exists), an import must not flip it back to auto_added=1
    or otherwise lose the subscribed signal."""
    alice = _make_user(user_repo, "alice@example.com")
    rss = "https://www.youtube.com/feeds/videos.xml?channel_id=UCuAXFkgsw1L7xaCfnd5JJOw"

    # Pre-seed the channel as a normal subscribed podcast.
    podcast_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO podcasts (id, created_at, updated_at, rss_url, title, slug,
                                  description, language, synthetic, auto_added)
            VALUES (?, ?, ?, ?, 'Rick Astley', 'rick-astley', '', 'en', 0, 0)
            """,
            (podcast_id, now, now, rss),
        )
        conn.commit()
    _follow(db_path, alice.id, podcast_id)

    svc = _service_with(repo, inbox_repo, queue, fake_youtube_video_info)
    svc.import_url(user_id=alice.id, url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, auto_added FROM podcasts WHERE rss_url = ?", (rss,)).fetchone()
        assert row["id"] == podcast_id  # same row, not a duplicate
        assert row["auto_added"] == 0  # NOT flipped back to auto_added
