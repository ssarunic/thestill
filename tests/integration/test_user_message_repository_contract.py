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

"""Dual-backend contract suite for the lifecycle message ledger (spec #118).

Same tests against SQLite and PostgreSQL: the candidate facts, the
send-once constraint, the leased claim and the settle transitions.
Postgres cases are skipped unless ``TEST_DATABASE_URL`` points at one:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_test \\
        ./venv/bin/python -m pytest tests/integration/test_user_message_repository_contract.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing import Briefing
from thestill.models.briefing_schedule import BriefingSchedule
from thestill.models.mcp_token import McpToken
from thestill.models.podcast import Podcast
from thestill.models.user import PodcastFollower, User, UserStatus
from thestill.models.user_message import MessageStatus

PG_DSN = os.getenv("TEST_DATABASE_URL", "")
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
WINDOW = dict(created_from=NOW - timedelta(days=30), created_to=NOW, channel="email", limit=50)


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


class _Env:
    def add_user(self, email: str, *, created_at: datetime = NOW - timedelta(days=2), tips: bool = True) -> User:
        self.users.save(User(email=email, created_at=created_at, product_tips_email_enabled=tips))
        return self.users.get_by_email(email)

    def follow(self, user_id: str) -> None:
        podcast_id = self.add_podcast(f"show-{uuid.uuid4().hex[:8]}")
        self.followers.add(PodcastFollower(user_id=user_id, podcast_id=podcast_id, created_at=NOW))


class _SqliteEnv(_Env):
    def __init__(self, db_path: str):
        from thestill.repositories.sqlite_briefing_delivery_repository import SqliteBriefingDeliveryRepository
        from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
        from thestill.repositories.sqlite_briefing_schedule_repository import SqliteBriefingScheduleRepository
        from thestill.repositories.sqlite_mcp_token_repository import SqliteMcpTokenRepository
        from thestill.repositories.sqlite_podcast_follower_repository import SqlitePodcastFollowerRepository
        from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
        from thestill.repositories.sqlite_user_message_repository import SqliteUserMessageRepository
        from thestill.repositories.sqlite_user_repository import SqliteUserRepository

        self.podcasts = SqlitePodcastRepository(db_path=db_path)  # owns the DDL
        self.users = SqliteUserRepository(db_path)
        self.followers = SqlitePodcastFollowerRepository(db_path)
        self.tokens = SqliteMcpTokenRepository(db_path)
        self.schedules = SqliteBriefingScheduleRepository(db_path)
        self.briefings = SqliteBriefingRepository(db_path)
        self.deliveries = SqliteBriefingDeliveryRepository(db_path)
        self.messages = SqliteUserMessageRepository(db_path)

    def add_podcast(self, slug: str) -> str:
        podcast_id = str(uuid.uuid4())
        self.podcasts.save(
            Podcast(id=podcast_id, rss_url=f"https://example.com/{slug}.xml", title=slug, description="")
        )
        return podcast_id


class _PostgresEnv(_Env):
    def __init__(self, dsn: str):
        import psycopg

        from thestill.repositories.postgres_briefing_delivery_repository import PostgresBriefingDeliveryRepository
        from thestill.repositories.postgres_briefing_repository import PostgresBriefingRepository
        from thestill.repositories.postgres_briefing_schedule_repository import PostgresBriefingScheduleRepository
        from thestill.repositories.postgres_mcp_token_repository import PostgresMcpTokenRepository
        from thestill.repositories.postgres_podcast_follower_repository import PostgresPodcastFollowerRepository
        from thestill.repositories.postgres_schema import ensure_schema
        from thestill.repositories.postgres_user_message_repository import PostgresUserMessageRepository
        from thestill.repositories.postgres_user_repository import PostgresUserRepository

        ensure_schema(dsn)
        with psycopg.connect(dsn) as conn:
            conn.execute("TRUNCATE users, podcasts CASCADE")
        self.dsn = dsn
        self.users = PostgresUserRepository(dsn)
        self.followers = PostgresPodcastFollowerRepository(dsn)
        self.tokens = PostgresMcpTokenRepository(dsn)
        self.schedules = PostgresBriefingScheduleRepository(dsn)
        self.briefings = PostgresBriefingRepository(dsn)
        self.deliveries = PostgresBriefingDeliveryRepository(dsn)
        self.messages = PostgresUserMessageRepository(dsn)

    def add_podcast(self, slug: str) -> str:
        import psycopg

        podcast_id = str(uuid.uuid4())
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                "INSERT INTO podcasts (id, rss_url, title) VALUES (%s, %s, %s)",
                (podcast_id, f"https://example.com/{slug}.xml", slug),
            )
        return podcast_id


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        return _SqliteEnv(str(tmp_path / "lifecycle.db"))
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    return _PostgresEnv(PG_DSN)


# -- candidates --------------------------------------------------------------


def test_candidates_carry_the_facts(env):
    user = env.add_user("a@example.com")
    env.follow(user.id)
    env.follow(user.id)
    env.tokens.upsert(McpToken(user_id=user.id, token_hash="h", token_prefix="p", created_at=NOW))
    env.schedules.upsert(
        BriefingSchedule(user_id=user.id, timezone_name="Europe/Zagreb", hour_local=7, email_enabled=True)
    )
    briefing = env.briefings.insert(
        Briefing(
            user_id=user.id,
            cursor_from=NOW - timedelta(days=1),
            cursor_to=NOW,
            episode_count=1,
            created_at=NOW,
        )
    )
    env.deliveries.ensure_pending(briefing.id, "email", now=NOW)
    delivery = env.deliveries.get_for_briefing(briefing.id, "email")
    env.deliveries.mark_sent(delivery.id, sent_at=NOW - timedelta(hours=3))
    env.messages.ensure_pending(user.id, "welcome", "email", now=NOW - timedelta(hours=1))

    [facts] = env.messages.candidates(**WINDOW)

    assert facts.user_id == user.id and facts.email == "a@example.com"
    assert facts.follow_count == 2
    assert facts.connector_connected is True
    assert facts.has_read is False
    assert facts.timezone_name == "Europe/Zagreb" and facts.briefing_hour_local == 7
    assert facts.briefing_enabled is True and facts.briefing_email_enabled is True
    assert facts.last_briefing_email_at == NOW - timedelta(hours=3)
    assert facts.last_message_at == NOW - timedelta(hours=1)
    assert facts.message_keys == frozenset({"welcome"})


def test_candidates_without_any_activity(env):
    env.add_user("new@example.com")

    [facts] = env.messages.candidates(**WINDOW)

    assert facts.follow_count == 0 and not facts.connector_connected
    assert facts.timezone_name is None and not facts.briefing_enabled
    assert facts.last_briefing_email_at is None and facts.last_message_at is None
    assert facts.message_keys == frozenset()


def test_revoked_connector_does_not_count(env):
    user = env.add_user("a@example.com")
    env.tokens.upsert(McpToken(user_id=user.id, token_hash="h", token_prefix="p", created_at=NOW, revoked_at=NOW))

    [facts] = env.messages.candidates(**WINDOW)

    assert facts.connector_connected is False


def test_candidates_exclude_tips_off_inactive_and_out_of_window(env):
    keep = env.add_user("keep@example.com")
    env.add_user("off@example.com", tips=False)
    env.add_user("old@example.com", created_at=NOW - timedelta(days=60))
    banned = env.add_user("banned@example.com")
    env.users.apply_status(banned.id, UserStatus.BANNED, reason="spam", changed_by=None, changed_at=NOW)

    assert [f.user_id for f in env.messages.candidates(**WINDOW)] == [keep.id]
    assert env.messages.candidates(**WINDOW, user_id=banned.id) == []


def test_tips_switch_round_trips(env):
    user = env.add_user("a@example.com")
    assert env.users.get_by_id(user.id).product_tips_email_enabled is True
    assert env.users.update_product_tips_email(user.id, False) is True
    assert env.users.get_by_id(user.id).product_tips_email_enabled is False
    assert env.messages.candidates(**WINDOW) == []


# -- ledger ------------------------------------------------------------------


def test_ensure_pending_is_send_once(env):
    user = env.add_user("a@example.com")

    assert env.messages.ensure_pending(user.id, "welcome", "email", now=NOW) is True
    assert env.messages.ensure_pending(user.id, "welcome", "email", now=NOW) is False
    assert env.messages.ensure_pending(user.id, "follow_nudge", "email", now=NOW) is True

    [row] = [r for r in env.messages.due(NOW, limit=10) if r.message_key == "welcome"]
    env.messages.mark_sent(row.id, sent_at=NOW)
    assert env.messages.ensure_pending(user.id, "welcome", "email", now=NOW) is False


def test_claim_lease_and_settle(env):
    user = env.add_user("a@example.com")
    env.messages.ensure_pending(user.id, "welcome", "email", now=NOW)
    [row] = env.messages.due(NOW, limit=10)

    assert env.messages.claim(row.id, now=NOW, lease_seconds=600) is True
    assert env.messages.claim(row.id, now=NOW, lease_seconds=600) is False  # leased
    assert env.messages.due(NOW, limit=10) == []
    assert [r.id for r in env.messages.due(NOW + timedelta(minutes=11), limit=10)] == [row.id]  # lease expired

    env.messages.mark_retry(row.id, attempts=1, next_attempt_at=NOW + timedelta(minutes=5), error="smtp down")
    retried = env.messages.get(row.id)
    assert retried.status == MessageStatus.PENDING and retried.attempts == 1 and retried.last_error == "smtp down"

    env.messages.mark_skipped(row.id, reason="tips off")
    skipped = env.messages.get(row.id)
    assert skipped.status == MessageStatus.SKIPPED and skipped.next_attempt_at is None
    assert env.messages.due(NOW + timedelta(days=1), limit=10) == []
    assert env.messages.status_counts() == {("welcome", "skipped"): 1}


def test_skipped_and_failed_rows_do_not_count_towards_the_cap(env):
    user = env.add_user("a@example.com")
    env.messages.ensure_pending(user.id, "welcome", "email", now=NOW - timedelta(hours=2))
    [row] = env.messages.due(NOW, limit=10)
    env.messages.mark_failed(row.id, attempts=3, error="bounced")

    [facts] = env.messages.candidates(**WINDOW)

    assert facts.last_message_at is None
    assert facts.message_keys == frozenset({"welcome"})


def test_deleting_the_user_deletes_their_messages(env):
    user = env.add_user("a@example.com")
    env.messages.ensure_pending(user.id, "welcome", "email", now=NOW)

    env.users.delete(user.id)

    assert env.messages.status_counts() == {}
