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

"""Unit tests for ``BriefingService`` (spec #36, Phase 1)."""

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.inbox import InboxEntry
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.briefing_renderer import BriefingRenderer
from thestill.services.briefing_service import BriefingNotFoundError, BriefingService
from thestill.services.digest_generator import DigestGenerator
from thestill.utils.path_manager import PathManager

# Six hours: matches the production default. Tests opt out of the throttle
# by passing ``force=True`` or by advancing ``now`` past this window.
THROTTLE_SECONDS = 6 * 60 * 60


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "briefing_service.db"
    SqlitePodcastRepository(str(path))
    return str(path)


@pytest.fixture
def user_repo(db_path):
    return SqliteUserRepository(db_path)


@pytest.fixture
def inbox_repo(db_path):
    return SqliteInboxRepository(db_path)


@pytest.fixture
def briefing_repo(db_path):
    return SqliteBriefingRepository(db_path)


@pytest.fixture
def service(briefing_repo, inbox_repo):
    return BriefingService(briefing_repo, inbox_repo, min_interval_seconds=THROTTLE_SECONDS)


@pytest.fixture
def path_manager(tmp_path):
    return PathManager(storage_path=str(tmp_path))


@pytest.fixture
def podcast_repo(db_path):
    return SqlitePodcastRepository(db_path)


@pytest.fixture
def renderer(path_manager, podcast_repo):
    return BriefingRenderer(
        DigestGenerator(path_manager),
        podcast_repo,
        path_manager,
    )


@pytest.fixture
def service_with_rendering(briefing_repo, inbox_repo, renderer):
    return BriefingService(
        briefing_repo,
        inbox_repo,
        min_interval_seconds=THROTTLE_SECONDS,
        renderer=renderer,
    )


def _make_user(user_repo, email: str) -> User:
    user = User(id=str(uuid.uuid4()), email=email, name=email.split("@")[0])
    user_repo.save(user)
    return user


def _publish_episode(db_path: str, podcast_id: str, title: str) -> str:
    """Create a podcast (if needed) and an episode that can be referenced
    from ``user_episode_inbox`` rows. Returns the episode_id."""
    ep_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO podcasts (id, rss_url, title, slug)
            VALUES (?, ?, ?, ?)
            """,
            (podcast_id, f"https://example.com/{podcast_id}.xml", "P", podcast_id[:8]),
        )
        conn.execute(
            """
            INSERT INTO episodes (
                id, podcast_id, external_id, title, slug, description,
                description_html, audio_url
            ) VALUES (?, ?, ?, ?, '', '', '', ?)
            """,
            (
                ep_id,
                podcast_id,
                f"ext-{title}",
                title,
                f"https://cdn.example.com/{title}.mp3",
            ),
        )
        conn.commit()
    return ep_id


def _deliver_to_inbox(
    inbox_repo: SqliteInboxRepository,
    *,
    user_id: str,
    episode_id: str,
    delivered_at: datetime,
    state: str = "unread",
    state_changed_at: datetime | None = None,
) -> None:
    inbox_repo.insert_many(
        [
            InboxEntry(
                user_id=user_id,
                episode_id=episode_id,
                source="follow_new",
                state=state,
                delivered_at=delivered_at,
                state_changed_at=state_changed_at,
            )
        ]
    )


# ============================================================================
# generate_for_user
# ============================================================================


def test_first_run_covers_inbox_from_epoch(service, db_path, user_repo, inbox_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(3):
        ep_id = _publish_episode(db_path, podcast_id, f"ep-{i}")
        _deliver_to_inbox(
            inbox_repo,
            user_id=user.id,
            episode_id=ep_id,
            delivered_at=base + timedelta(minutes=i),
        )

    briefing = service.generate_for_user(user.id, now=base + timedelta(hours=1))

    assert briefing is not None
    assert briefing.episode_count == 3
    # cursor_from defaults to epoch on first run.
    assert briefing.cursor_from.year == 1970
    assert briefing.cursor_to == base + timedelta(hours=1)
    assert briefing.script_path is None  # Phase 1: rendering deferred.


def test_returns_none_when_inbox_empty(service, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert service.generate_for_user(user.id) is None


def test_excludes_read_and_dismissed_items(service, db_path, user_repo, inbox_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    state_changed = base + timedelta(minutes=10)
    states = [("unread", None), ("saved", None), ("read", state_changed), ("dismissed", state_changed)]
    for i, (state, changed) in enumerate(states):
        ep_id = _publish_episode(db_path, podcast_id, f"ep-{state}")
        _deliver_to_inbox(
            inbox_repo,
            user_id=user.id,
            episode_id=ep_id,
            delivered_at=base + timedelta(minutes=i),
            state=state,
            state_changed_at=changed,
        )

    briefing = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert briefing is not None
    # Only ``unread`` + ``saved`` count.
    assert briefing.episode_count == 2


def test_throttle_returns_existing_briefing(service, db_path, user_repo, inbox_repo):
    """Re-running within the throttle window returns the same briefing id."""
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_id = _publish_episode(db_path, podcast_id, "ep-1")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_id, delivered_at=base)

    first = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert first is not None

    # Advance only 1 hour — well inside the 6h throttle.
    second = service.generate_for_user(user.id, now=base + timedelta(hours=2))
    assert second is not None
    assert second.id == first.id


def test_force_bypasses_throttle(service, db_path, user_repo, inbox_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep1 = _publish_episode(db_path, podcast_id, "ep-1")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep1, delivered_at=base)

    first = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert first is not None

    # New eligible item arrives, then we force-regenerate inside the window.
    ep2 = _publish_episode(db_path, podcast_id, "ep-2")
    _deliver_to_inbox(
        inbox_repo,
        user_id=user.id,
        episode_id=ep2,
        delivered_at=base + timedelta(hours=1, minutes=30),
    )

    second = service.generate_for_user(user.id, now=base + timedelta(hours=2), force=True)
    assert second is not None
    assert second.id != first.id
    # cursor_from on the second briefing picks up where the first ended.
    assert second.cursor_from == first.cursor_to
    assert second.episode_count == 1


def test_cursor_advances_across_briefings(service, db_path, user_repo, inbox_repo):
    """Second briefing covers only items delivered after the first."""
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    ep1 = _publish_episode(db_path, podcast_id, "ep-1")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep1, delivered_at=base)

    first = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert first is not None
    assert first.episode_count == 1

    ep2 = _publish_episode(db_path, podcast_id, "ep-2")
    _deliver_to_inbox(
        inbox_repo,
        user_id=user.id,
        episode_id=ep2,
        delivered_at=base + timedelta(hours=8),
    )

    # Now past the throttle (6h default) and a new item is in the window.
    second = service.generate_for_user(user.id, now=base + timedelta(hours=10))
    assert second is not None
    assert second.id != first.id
    assert second.cursor_from == first.cursor_to
    assert second.episode_count == 1


def test_returns_none_when_only_throttled_inbox_is_empty_after_cursor(service, db_path, user_repo, inbox_repo):
    """No new items past the previous cursor → no briefing, even after throttle."""
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_id = _publish_episode(db_path, podcast_id, "ep-1")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_id, delivered_at=base)

    first = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert first is not None

    # No new inbox items; throttle elapsed.
    second = service.generate_for_user(user.id, now=base + timedelta(hours=10))
    assert second is None


# ============================================================================
# latest_for_user / mark_listened
# ============================================================================


def test_latest_for_user_returns_most_recent_briefing(service, db_path, user_repo, inbox_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_id = _publish_episode(db_path, podcast_id, "ep")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_id, delivered_at=base)

    generated = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert generated is not None

    latest = service.latest_for_user(user.id)
    assert latest is not None
    assert latest.id == generated.id


def test_latest_for_user_returns_none_when_user_has_no_briefings(service, user_repo):
    user = _make_user(user_repo, "alice@example.com")
    assert service.latest_for_user(user.id) is None


def test_mark_listened_sets_timestamp(service, db_path, user_repo, inbox_repo):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_id = _publish_episode(db_path, podcast_id, "ep")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_id, delivered_at=base)

    briefing = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert briefing is not None

    listened_at = base + timedelta(hours=2)
    updated = service.mark_listened(briefing.id, now=listened_at)
    assert updated.listened_at == listened_at


def test_mark_listened_raises_for_unknown_briefing(service):
    with pytest.raises(BriefingNotFoundError):
        service.mark_listened("00000000-0000-0000-0000-000000000000")


# ============================================================================
# Rendering (Phase 1.5)
# ============================================================================


def test_generate_for_user_with_renderer_writes_script_and_persists_path(
    service_with_rendering, briefing_repo, db_path, user_repo, inbox_repo, path_manager
):
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(2):
        ep_id = _publish_episode(db_path, podcast_id, f"Episode-{i}")
        _deliver_to_inbox(
            inbox_repo,
            user_id=user.id,
            episode_id=ep_id,
            delivered_at=base + timedelta(minutes=i),
        )

    briefing = service_with_rendering.generate_for_user(user.id, now=base + timedelta(hours=1))

    assert briefing is not None
    assert briefing.script_path is not None
    expected_dir = path_manager.briefings_dir() / user.id / briefing.id
    expected_path = expected_dir / "script.md"
    assert briefing.script_path == str(expected_path)
    assert expected_path.exists()
    contents = expected_path.read_text(encoding="utf-8")
    assert "Episode-0" in contents and "Episode-1" in contents

    # ``script_path`` round-trips through the repo as well.
    refetched = briefing_repo.get(briefing.id)
    assert refetched is not None
    assert refetched.script_path == str(expected_path)


def test_generate_for_user_without_renderer_leaves_script_path_null(service, db_path, user_repo, inbox_repo):
    """The renderer is optional; without it the row persists with script_path=None."""
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_id = _publish_episode(db_path, podcast_id, "ep")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_id, delivered_at=base)

    briefing = service.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert briefing is not None
    assert briefing.script_path is None


def test_generate_for_user_skips_episodes_deleted_between_delivery_and_render(
    service_with_rendering, db_path, user_repo, inbox_repo, path_manager
):
    """A removed episode is logged and skipped; the briefing still lands."""
    user = _make_user(user_repo, "alice@example.com")
    podcast_id = str(uuid.uuid4())
    base = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    ep_keep = _publish_episode(db_path, podcast_id, "Kept")
    ep_drop = _publish_episode(db_path, podcast_id, "Dropped")
    _deliver_to_inbox(inbox_repo, user_id=user.id, episode_id=ep_keep, delivered_at=base)
    _deliver_to_inbox(
        inbox_repo,
        user_id=user.id,
        episode_id=ep_drop,
        delivered_at=base + timedelta(minutes=1),
    )

    # Simulate deletion of one episode between delivery and render.
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM episodes WHERE id = ?", (ep_drop,))
        conn.commit()

    briefing = service_with_rendering.generate_for_user(user.id, now=base + timedelta(hours=1))
    assert briefing is not None
    # episode_count reflects what was *delivered*, not what survived to render.
    assert briefing.episode_count == 2
    rendered = (path_manager.briefings_dir() / user.id / briefing.id / "script.md").read_text("utf-8")
    assert "Kept" in rendered
    assert "Dropped" not in rendered


# ============================================================================
# Constructor / from_config
# ============================================================================


def test_constructor_rejects_negative_min_interval(briefing_repo, inbox_repo):
    with pytest.raises(ValueError):
        BriefingService(briefing_repo, inbox_repo, min_interval_seconds=-1)


def test_from_config_pulls_min_interval(briefing_repo, inbox_repo):
    class _StubConfig:
        briefing_min_interval_seconds = 3600

    service = BriefingService.from_config(_StubConfig(), briefing_repo, inbox_repo)
    assert service._min_interval == timedelta(seconds=3600)
