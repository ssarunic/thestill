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

"""Dual-backend contract suite for the spec #104 Phase 1 persistence.

Settings, the audit log, account status columns, the follow gate's locked
transaction, suspended follows, the user directory and the per-podcast task
cancel — asserted identically on SQLite and a real Postgres (spec #42 FM-5).

The Postgres cases are skipped when no server is reachable:

    TEST_DATABASE_URL=postgresql://localhost/thestill_test104 \\
        ./venv/bin/python -m pytest tests/integration/test_abuse_repositories_contract.py
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import pytest

from thestill.core.queue_manager import QueueManager, TaskStage, TaskStatus
from thestill.models.podcast import Episode, Podcast
from thestill.models.user import PodcastFollower, User, UserStatus
from thestill.repositories.audit_log_repository import AuditEntry
from thestill.repositories.podcast_follower_repository import ALLOW_FOLLOW, FollowDecision
from thestill.services.abuse.errors import FeaturePausedError

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)


class _Env:
    """One backend's repositories plus seeding helpers."""

    backend: str

    def add_user(self, email: str, *, is_admin: bool = False) -> User:
        user = self.users.save(User(email=email, is_admin=is_admin, created_at=NOW))
        return self.users.get_by_email(user.email)

    def follow(self, user_id: str, podcast_id: str, *, at: datetime = NOW) -> None:
        self.followers.add(PodcastFollower(user_id=user_id, podcast_id=podcast_id, created_at=at))


class _SqliteEnv(_Env):
    backend = "sqlite"

    def __init__(self, db_path: str):
        from thestill.repositories.sqlite_audit_log_repository import SqliteAuditLogRepository
        from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
        from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
        from thestill.repositories.sqlite_settings_repository import SqliteSettingsRepository
        from thestill.repositories.sqlite_suspended_follow_repository import SqliteSuspendedFollowRepository
        from thestill.repositories.sqlite_user_directory_repository import SqliteUserDirectoryRepository
        from thestill.repositories.sqlite_user_repository import SqliteUserRepository

        self.podcasts = SqlitePodcastRepository(db_path=db_path)  # owns the DDL
        self.users = SqliteUserRepository(db_path)
        self.followers = SqlitePodcastFollowerRepository(db_path)
        self.suspended = SqliteSuspendedFollowRepository(db_path)
        self.settings = SqliteSettingsRepository(db_path)
        self.audit = SqliteAuditLogRepository(db_path)
        self.directory = SqliteUserDirectoryRepository(db_path)
        self.queue = QueueManager(db_path)
        self.make_follower_repo = lambda: SqlitePodcastFollowerRepository(db_path)

    def add_podcast(self, slug: str, episodes: int = 0) -> Tuple[str, List[str]]:
        podcast_id = str(uuid.uuid4())
        episode_ids = [str(uuid.uuid4()) for _ in range(episodes)]
        self.podcasts.save(
            Podcast(
                id=podcast_id,
                rss_url=f"https://example.com/{slug}.xml",
                title=slug,
                description="",
                episodes=[
                    Episode(
                        id=eid,
                        external_id=f"{slug}-{i}",
                        title=f"{slug} {i}",
                        description="",
                        pub_date=NOW,
                        audio_url=f"https://example.com/{slug}/{i}.mp3",
                        duration=60,
                    )
                    for i, eid in enumerate(episode_ids)
                ],
            )
        )
        return podcast_id, episode_ids


class _PostgresEnv(_Env):
    backend = "postgres"

    def __init__(self, dsn: str):
        import psycopg

        from thestill.core.postgres_queue_manager import PostgresQueueManager
        from thestill.repositories.postgres_audit_log_repository import PostgresAuditLogRepository
        from thestill.repositories.postgres_podcast_follower_repository import PostgresPodcastFollowerRepository
        from thestill.repositories.postgres_schema import ensure_schema
        from thestill.repositories.postgres_settings_repository import PostgresSettingsRepository
        from thestill.repositories.postgres_suspended_follow_repository import PostgresSuspendedFollowRepository
        from thestill.repositories.postgres_user_directory_repository import PostgresUserDirectoryRepository
        from thestill.repositories.postgres_user_repository import PostgresUserRepository

        ensure_schema(dsn)
        with psycopg.connect(dsn) as conn:
            conn.execute("TRUNCATE users, podcasts, tasks, app_settings, admin_audit_log CASCADE")
        self.dsn = dsn
        self.users = PostgresUserRepository(dsn)
        self.followers = PostgresPodcastFollowerRepository(dsn)
        self.suspended = PostgresSuspendedFollowRepository(dsn)
        self.settings = PostgresSettingsRepository(dsn)
        self.audit = PostgresAuditLogRepository(dsn)
        self.directory = PostgresUserDirectoryRepository(dsn)
        self.queue = PostgresQueueManager(dsn)
        self.make_follower_repo = lambda: PostgresPodcastFollowerRepository(dsn)

    def add_podcast(self, slug: str, episodes: int = 0) -> Tuple[str, List[str]]:
        import psycopg

        podcast_id = str(uuid.uuid4())
        episode_ids = [str(uuid.uuid4()) for _ in range(episodes)]
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
                (podcast_id, f"https://example.com/{slug}.xml", slug),
            )
            for i, eid in enumerate(episode_ids):
                conn.execute(
                    "INSERT INTO episodes (id, podcast_id, external_id, title, audio_url, duration)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (eid, podcast_id, f"{slug}-{i}", f"{slug} {i}", f"https://example.com/{slug}/{i}.mp3", "60"),
                )
        return podcast_id, episode_ids


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        return _SqliteEnv(str(tmp_path / "abuse.db"))
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    return _PostgresEnv(PG_DSN)


# ---------------------------------------------------------------------------
# app_settings
# ---------------------------------------------------------------------------
def test_settings_upsert_get_delete(env):
    admin = env.add_user("admin@example.com", is_admin=True)
    assert env.settings.get_all() == {}

    env.settings.upsert("signup_mode", "closed", updated_by=admin.id, updated_at=NOW)
    env.settings.upsert("signup_allowlist", ["@example.com"], updated_by=None, updated_at=NOW)
    env.settings.upsert("signup_mode", "allowlist", updated_by=None, updated_at=NOW + timedelta(minutes=1))

    rows = env.settings.get_all()
    assert rows["signup_mode"].value == "allowlist"
    assert rows["signup_mode"].updated_by is None
    assert rows["signup_mode"].updated_at == NOW + timedelta(minutes=1)
    assert rows["signup_allowlist"].value == ["@example.com"]

    assert env.settings.delete("signup_mode") is True
    assert env.settings.delete("signup_mode") is False
    assert set(env.settings.get_all()) == {"signup_allowlist"}


# ---------------------------------------------------------------------------
# admin_audit_log
# ---------------------------------------------------------------------------
def test_audit_log_is_newest_first_and_filterable(env):
    target = env.add_user("target@example.com")
    for minutes, action in ((0, "user.suspend"), (1, "setting.set"), (2, "user.reinstate")):
        env.audit.append(
            AuditEntry(
                action=action,
                actor="cli",
                created_at=NOW + timedelta(minutes=minutes),
                target_user_id=target.id if action.startswith("user.") else None,
                details={"reason": "spam", "n": minutes},
            )
        )

    entries = env.audit.list()
    assert [e.action for e in entries] == ["user.reinstate", "setting.set", "user.suspend"]
    assert entries[-1].details == {"reason": "spam", "n": 0}
    assert entries[-1].created_at == NOW
    assert env.audit.count() == 3
    assert [e.action for e in env.audit.list(target_user_id=target.id)] == ["user.reinstate", "user.suspend"]
    assert env.audit.count(action="setting.set") == 1
    assert [e.action for e in env.audit.list(limit=1, offset=1)] == ["setting.set"]


# ---------------------------------------------------------------------------
# users: status columns
# ---------------------------------------------------------------------------
def test_new_users_are_active(env):
    user = env.add_user("new@example.com")
    assert user.status == UserStatus.ACTIVE
    assert user.is_active
    assert user.tokens_valid_after is None


def test_apply_status_and_save_never_lifts_it(env):
    admin = env.add_user("admin@example.com", is_admin=True)
    user = env.add_user("spam@example.com")
    valid_after = NOW + timedelta(seconds=1)

    assert env.users.apply_status(
        user.id,
        UserStatus.BANNED,
        reason="spam",
        changed_by=admin.id,
        changed_at=NOW,
        tokens_valid_after=valid_after,
    )
    # A login re-saves the row: the upsert must not touch the status columns.
    login = env.users.get_by_id(user.id)
    login.last_login_at = NOW + timedelta(hours=1)
    login.status = UserStatus.ACTIVE
    env.users.save(login)

    stored = env.users.get_by_id(user.id)
    assert stored.status == UserStatus.BANNED
    assert stored.status_reason == "spam"
    assert stored.status_changed_by == admin.id
    assert stored.status_changed_at == NOW
    assert stored.tokens_valid_after == valid_after

    # Reinstating without a new revocation keeps the old one.
    env.users.apply_status(user.id, UserStatus.ACTIVE, reason=None, changed_by=None, changed_at=NOW)
    stored = env.users.get_by_id(user.id)
    assert stored.is_active and stored.tokens_valid_after == valid_after


def test_set_admin_and_count_active_admins(env):
    a = env.add_user("a@example.com", is_admin=True)
    b = env.add_user("b@example.com")
    assert env.directory.count_admins() == 1
    assert env.users.set_admin(b.id, True)
    assert env.directory.count_admins() == 2
    # A suspended admin can't run the console, so doesn't count.
    env.users.apply_status(a.id, UserStatus.SUSPENDED, reason="x", changed_by=None, changed_at=NOW)
    assert env.directory.count_admins() == 1
    assert env.users.set_admin(b.id, False)
    assert env.directory.count_admins() == 0


# ---------------------------------------------------------------------------
# follow_with_gate
# ---------------------------------------------------------------------------
def test_follow_with_gate_inserts_and_reports_introduced(env):
    u1, u2 = env.add_user("u1@example.com"), env.add_user("u2@example.com")
    pid, _ = env.add_podcast("show")
    seen = []

    def decide(facts):
        seen.append(facts)
        return ALLOW_FOLLOW

    first = env.followers.follow_with_gate(PodcastFollower(user_id=u1.id, podcast_id=pid), decide)
    second = env.followers.follow_with_gate(PodcastFollower(user_id=u2.id, podcast_id=pid), decide)

    assert first.introduced and not second.introduced
    assert [f.podcast_followers for f in seen] == [0, 1]
    assert env.followers.count_by_podcast(pid) == 2


def test_follow_with_gate_refusal_writes_nothing(env):
    user = env.add_user("u@example.com")
    pid, _ = env.add_podcast("dormant")

    with pytest.raises(FeaturePausedError):
        env.followers.follow_with_gate(
            PodcastFollower(user_id=user.id, podcast_id=pid),
            lambda facts: FollowDecision(refusal=FeaturePausedError("new_feeds")),
        )
    assert env.followers.count_by_podcast(pid) == 0


def test_follow_with_gate_already_following_skips_decider(env):
    user = env.add_user("u@example.com")
    pid, _ = env.add_podcast("show")
    env.follow(user.id, pid)

    def boom(facts):
        raise AssertionError("decider must not run for an existing follow")

    outcome = env.followers.follow_with_gate(PodcastFollower(user_id=user.id, podcast_id=pid), boom)
    assert outcome.already_following and outcome.follower is None


def test_follow_with_gate_serialises_first_follows(env):
    """A decider that admits only a feed's first follower lets exactly one
    of N concurrent first follows through: the lock makes each transaction
    see the others' committed rows."""
    users = [env.add_user(f"racer{i}@example.com") for i in range(6)]
    pid, _ = env.add_podcast("contested")

    def only_first(facts):
        return ALLOW_FOLLOW if facts.podcast_followers == 0 else FollowDecision(refusal=FeaturePausedError("new_feeds"))

    barrier = threading.Barrier(len(users))
    results = []

    def attempt(user):
        repo = env.make_follower_repo()  # own connection per thread
        barrier.wait()
        try:
            repo.follow_with_gate(PodcastFollower(user_id=user.id, podcast_id=pid), only_first)
            results.append("ok")
        except FeaturePausedError:
            results.append("refused")

    threads = [threading.Thread(target=attempt, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == ["ok"] + ["refused"] * (len(users) - 1)
    assert env.followers.count_by_podcast(pid) == 1


# ---------------------------------------------------------------------------
# suspended_follows + introduced feeds
# ---------------------------------------------------------------------------
def test_move_aside_then_restore_keeps_created_at(env):
    user, other = env.add_user("banned@example.com"), env.add_user("other@example.com")
    solo, _ = env.add_podcast("solo")
    shared, _ = env.add_podcast("shared")
    early = NOW - timedelta(days=30)
    env.follow(user.id, solo, at=early)
    env.follow(user.id, shared, at=early)
    env.follow(other.id, shared)

    moved = env.suspended.move_follows_aside(user.id, now=NOW)
    assert sorted(moved) == sorted([solo, shared])
    assert env.followers.get_followed_podcast_ids(user.id) == []
    assert env.followers.count_by_podcast(solo) == 0
    assert env.followers.count_by_podcast(shared) == 1
    assert sorted(env.suspended.list_podcast_ids(user.id)) == sorted([solo, shared])
    # Idempotent: a second move finds nothing.
    assert env.suspended.move_follows_aside(user.id, now=NOW) == []

    restored = env.suspended.restore_follows(user.id)
    assert sorted(restored) == sorted([solo, shared])
    assert env.suspended.list_podcast_ids(user.id) == []
    follows = {f.podcast_id: f.created_at for f in env.followers.get_by_user(user.id)}
    assert follows == {solo: early, shared: early}
    assert env.suspended.restore_follows(user.id) == []


def test_introduced_podcast_ids_counts_moved_aside_follows(env):
    first, later = env.add_user("first@example.com"), env.add_user("later@example.com")
    a, _ = env.add_podcast("a")
    b, _ = env.add_podcast("b")
    env.follow(first.id, a, at=NOW - timedelta(days=2))
    env.follow(later.id, a, at=NOW)
    env.follow(later.id, b, at=NOW - timedelta(days=5))
    env.follow(first.id, b, at=NOW)

    assert env.followers.introduced_podcast_ids(first.id) == [a]
    assert env.followers.introduced_podcast_ids(later.id) == [b]
    # A ban moves the rows aside; the history still counts.
    env.suspended.move_follows_aside(first.id, now=NOW)
    assert env.followers.introduced_podcast_ids(first.id) == [a]

    env.suspended.discard(first.id, [a])
    assert env.followers.introduced_podcast_ids(first.id) == []


# ---------------------------------------------------------------------------
# user directory
# ---------------------------------------------------------------------------
def test_user_directory_lists_filters_and_sorts(env):
    alice = env.add_user("alice@example.com")
    bob = env.add_user("bob@other.org")
    carol = env.add_user("carol@example.com")
    p1, _ = env.add_podcast("p1")
    p2, _ = env.add_podcast("p2")
    env.follow(bob.id, p1)
    env.follow(bob.id, p2)
    env.follow(carol.id, p1)
    env.users.apply_status(carol.id, UserStatus.SUSPENDED, reason="x", changed_by=None, changed_at=NOW)

    by_follows = env.directory.list_users(sort="follows")
    assert [(s.user.email, s.follows) for s in by_follows][:2] == [("bob@other.org", 2), ("carol@example.com", 1)]
    assert [s.user.email for s in env.directory.list_users(status=UserStatus.SUSPENDED)] == ["carol@example.com"]
    assert {s.user.email for s in env.directory.list_users(q="EXAMPLE.com")} == {
        "alice@example.com",
        "carol@example.com",
    }
    assert env.directory.count_users() == 3
    assert env.directory.count_users(q="bob") == 1
    assert len(env.directory.list_users(limit=1, offset=1)) == 1
    assert alice.id in {s.user.id for s in env.directory.list_users()}


def test_user_search_takes_wildcards_literally(env):
    env.add_user("a_b@example.com")
    env.add_user("axb@example.com")
    env.add_user("100%@example.com")
    assert [s.user.email for s in env.directory.list_users(q="a_b")] == ["a_b@example.com"]
    assert [s.user.email for s in env.directory.list_users(q="0%@")] == ["100%@example.com"]


# ---------------------------------------------------------------------------
# queue: cancel a feed's queued work
# ---------------------------------------------------------------------------
def test_cancel_queued_for_podcasts_leaves_running_and_other_feeds(env):
    target, target_eps = env.add_podcast("target", episodes=3)
    other, other_eps = env.add_podcast("other", episodes=1)
    running = env.queue.add_task(target_eps[0], TaskStage.DOWNLOAD, priority=10)
    claimed = env.queue.get_next_task(stage=TaskStage.DOWNLOAD)
    assert claimed is not None and claimed.id == running.id
    queued = [env.queue.add_task(eid, TaskStage.DOWNLOAD) for eid in target_eps[1:]]
    untouched = env.queue.add_task(other_eps[0], TaskStage.DOWNLOAD)

    assert env.queue.cancel_queued_for_podcasts([target]) == 2
    assert env.queue.cancel_queued_for_podcasts([]) == 0

    assert env.queue.get_task(running.id).status == TaskStatus.PROCESSING
    assert {env.queue.get_task(t.id).status for t in queued} == {TaskStatus.SUPERSEDED}
    assert env.queue.get_task(untouched.id).status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Review fixes: the last-admin guard is atomic; unwanted episodes stop chaining
# ---------------------------------------------------------------------------
def test_concurrent_modifies_keep_every_change(env):
    """Two workers editing different users' quota overrides: each change is
    read-modify-written under a lock, so neither erases the other."""
    import time

    make_repo = lambda: type(env.settings)(env.podcasts.db_path if env.backend == "sqlite" else env.dsn)  # noqa: E731
    env.settings.upsert("quota_overrides", {"seed": {"imports_per_day": 1}}, updated_by=None, updated_at=NOW)
    barrier = threading.Barrier(4)

    def edit(user_id):
        repo = make_repo()
        barrier.wait()

        def change(current):
            time.sleep(0.05)  # widen the read → write window
            return {**(current or {}), user_id: {"imports_per_day": 9}}

        repo.modify("quota_overrides", change, updated_by=None, updated_at=NOW)

    threads = [threading.Thread(target=edit, args=(f"user-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert set(env.settings.get_all()["quota_overrides"].value) == {"seed", "user-0", "user-1", "user-2", "user-3"}


def test_concurrent_demotions_keep_one_active_admin(env):
    from thestill.repositories.user_repository import LastActiveAdminError

    a, b = env.add_user("a@example.com", is_admin=True), env.add_user("b@example.com", is_admin=True)
    make_repo = (
        (lambda: type(env.users)(env.podcasts.db_path))
        if env.backend == "sqlite"
        else (lambda: type(env.users)(env.dsn))
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def demote(user, how):
        repo = make_repo()
        barrier.wait()
        try:
            if how == "revoke":
                repo.set_admin(user.id, False, keep_an_active_admin=True)
            else:
                repo.apply_status(
                    user.id, UserStatus.BANNED, reason="x", changed_by=None, changed_at=NOW, keep_an_active_admin=True
                )
            outcomes.append("ok")
        except LastActiveAdminError:
            outcomes.append("refused")

    threads = [threading.Thread(target=demote, args=(a, "revoke")), threading.Thread(target=demote, args=(b, "ban"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes) == ["ok", "refused"]
    assert env.directory.count_admins() == 1


def test_reinstating_needs_no_admin_guard(env):
    a = env.add_user("a@example.com", is_admin=True)
    env.users.apply_status(
        a.id, UserStatus.ACTIVE, reason=None, changed_by=None, changed_at=NOW, keep_an_active_admin=True
    )
    assert env.directory.count_admins() == 1


def _inbox(env, user_id, episode_id, source):
    from thestill.models.inbox import InboxEntry

    if env.backend == "sqlite":
        from thestill.repositories.sqlite_inbox_repository import SqliteInboxRepository

        repo = SqliteInboxRepository(env.podcasts.db_path)
    else:
        from thestill.repositories.postgres_inbox_repository import PostgresInboxRepository

        repo = PostgresInboxRepository(env.dsn)
    repo.insert_many([InboxEntry(user_id=user_id, episode_id=episode_id, source=source)])


def test_episode_unwanted_once_nobody_follows_or_asked_for_it(env):
    banned, reader = env.add_user("banned@example.com"), env.add_user("reader@example.com")
    pid, [episode] = env.add_podcast("show", episodes=1)
    env.follow(banned.id, pid)
    assert env.queue.episode_unwanted(episode) is False  # followed

    env.users.apply_status(banned.id, UserStatus.BANNED, reason="x", changed_by=None, changed_at=NOW)
    env.suspended.move_follows_aside(banned.id, now=NOW)
    _inbox(env, banned.id, episode, "import")  # a banned user's own import doesn't count
    assert env.queue.episode_unwanted(episode) is True

    _inbox(env, reader.id, episode, "ad_hoc")  # an active user sent it to their inbox
    assert env.queue.episode_unwanted(episode) is False
