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

"""Dual-backend contract suite for the spec #104 Phase 2 usage ledger.

Includes the concurrency cases the spec calls for: with one allowance
left, N parallel reservations (each on its own connection) let exactly one
through and record N-1 refusals; follows by several users of overlapping
feeds finish without deadlock.

    TEST_DATABASE_URL=postgresql://localhost/thestill_test104 \\
        ./venv/bin/python -m pytest tests/integration/test_usage_ledger_contract.py
"""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from tests.integration.test_abuse_repositories_contract import NOW, PG_DSN, PG_OK, _PostgresEnv, _SqliteEnv
from thestill.models.user import PodcastFollower
from thestill.repositories.podcast_follower_repository import FollowDecision
from thestill.repositories.usage_event_repository import UsageRecord
from thestill.services.abuse.errors import QuotaExceededError


class _UsageMixin:
    def usage_repo(self):
        if self.backend == "sqlite":
            from thestill.repositories.sqlite_usage_event_repository import SqliteUsageEventRepository

            return SqliteUsageEventRepository(self.db_path)
        from thestill.repositories.postgres_usage_event_repository import PostgresUsageEventRepository

        return PostgresUsageEventRepository(self.dsn)


class _Sqlite(_SqliteEnv, _UsageMixin):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.db_path = db_path
        self.usage = self.usage_repo()


class _Postgres(_PostgresEnv, _UsageMixin):
    def __init__(self, dsn):
        super().__init__(dsn)
        self.usage = self.usage_repo()


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        return _Sqlite(str(tmp_path / "usage.db"))
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    return _Postgres(PG_DSN)


DAY_AGO = NOW - timedelta(hours=24)


def _reserve(env, user_id, *, limit, now=NOW, repo=None, kind="translation"):
    return (repo or env.usage).reserve(
        user_id,
        kind,
        quota="translations_per_day",
        limit=limit,
        since=now - timedelta(hours=24),
        lock_group="translation",
        subject_id="ep-1",
        now=now,
    )


def test_reserve_counts_pending_and_refuses_at_the_limit(env):
    user = env.add_user("u@example.com")
    first = _reserve(env, user.id, limit=2)
    assert first.event_id and first.count == 0
    # A pending reservation counts: the second sees one already in flight.
    second = _reserve(env, user.id, limit=2, now=NOW + timedelta(minutes=1))
    assert second.event_id and second.count == 1

    refused = _reserve(env, user.id, limit=2, now=NOW + timedelta(minutes=2))
    assert refused.event_id is None and refused.count == 2 and refused.frees_at == NOW
    counts = env.usage.counts_by_kind(user.id, DAY_AGO)
    assert counts == {"translation": 2, "quota_exceeded": 1}


def test_frees_at_accounts_for_a_lowered_limit(env):
    user = env.add_user("u@example.com")
    for minute in range(3):
        env.usage.settle(
            _reserve(env, user.id, limit=0, now=NOW + timedelta(minutes=minute)).event_id, "committed", now=NOW
        )
    # Limit lowered to 1 with 3 counted: all three must leave the window
    # before one is allowed, so the wait ends with the newest — not the
    # oldest, which the plain "oldest row" rule would have said.
    refused = _reserve(env, user.id, limit=1, now=NOW + timedelta(minutes=5))
    assert refused.event_id is None and refused.frees_at == NOW + timedelta(minutes=2)
    assert _reserve(env, user.id, limit=2, now=NOW + timedelta(minutes=6)).frees_at == NOW + timedelta(minutes=1)


def test_unlimited_still_records(env):
    user = env.add_user("u@example.com")
    for minute in range(3):
        assert _reserve(env, user.id, limit=0, now=NOW + timedelta(minutes=minute)).event_id
    assert env.usage.window(user.id, "translation", DAY_AGO)[0] == 3


def test_settle_release_stops_counting_and_settles_once(env):
    user = env.add_user("u@example.com")
    held = _reserve(env, user.id, limit=1).event_id
    assert _reserve(env, user.id, limit=1).event_id is None

    assert env.usage.settle(held, "released", now=NOW) is True
    assert env.usage.settle(held, "committed", now=NOW) is False  # already settled
    again = _reserve(env, user.id, limit=1)
    assert again.event_id is not None
    assert env.usage.settle(again.event_id, "committed", now=NOW, subject_id="episode-9") is True
    assert env.usage.window(user.id, "translation", DAY_AGO)[0] == 1


def test_window_rolls_over_24_hours(env):
    user = env.add_user("u@example.com")
    env.usage.settle(_reserve(env, user.id, limit=1, now=NOW).event_id, "committed", now=NOW)
    assert _reserve(env, user.id, limit=1, now=NOW + timedelta(hours=23)).event_id is None
    assert _reserve(env, user.id, limit=1, now=NOW + timedelta(hours=24, seconds=1)).event_id


def test_release_stale_and_prune(env):
    user = env.add_user("u@example.com")
    stale = _reserve(env, user.id, limit=0, now=NOW - timedelta(hours=1)).event_id
    fresh = _reserve(env, user.id, limit=0, now=NOW).event_id
    assert env.usage.release_stale(NOW - timedelta(minutes=15), now=NOW) == 1
    assert env.usage.window(user.id, "translation", DAY_AGO)[0] == 1
    assert env.usage.settle(stale, "committed", now=NOW) is False
    assert env.usage.settle(fresh, "committed", now=NOW) is True

    old = NOW - timedelta(days=100)
    env.usage.record(user.id, [UsageRecord("follow", "p1"), UsageRecord("feed_introduced", "p1")], now=old)
    assert env.usage.prune(NOW - timedelta(days=90)) == 1
    assert env.usage.counts_by_kind(user.id, old - timedelta(days=1)) == {
        "translation": 1,
        "feed_introduced": 1,
    }


def test_parallel_reservations_with_one_allowance_left(env):
    user = env.add_user("u@example.com")
    env.usage.settle(_reserve(env, user.id, limit=3).event_id, "committed", now=NOW)
    env.usage.settle(_reserve(env, user.id, limit=3).event_id, "committed", now=NOW)

    n = 8
    barrier = threading.Barrier(n)
    results = []

    def attempt():
        repo = env.usage_repo()  # own connection per thread
        barrier.wait()
        results.append(_reserve(env, user.id, limit=3, repo=repo).event_id)

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sum(1 for r in results if r) == 1
    assert env.usage.counts_by_kind(user.id, DAY_AGO)["quota_exceeded"] == n - 1


# ---------------------------------------------------------------------------
# follow_with_gate + ledger
# ---------------------------------------------------------------------------
def _limit_follows(limit):
    def decide(facts):
        if facts.follows_in_window >= limit:
            return FollowDecision(
                refusal=QuotaExceededError("follows_per_day", limit, 60),
                refusal_records=(UsageRecord("quota_exceeded", "follows_per_day"),),
            )
        records = [UsageRecord("follow", "x")]
        if facts.podcast_followers == 0:
            records.append(UsageRecord("feed_introduced", "x"))
        return FollowDecision(usage_records=tuple(records))

    return decide


def test_follow_with_gate_writes_ledger_rows_atomically(env):
    a, b = env.add_user("a@example.com"), env.add_user("b@example.com")
    p1, _ = env.add_podcast("p1")
    p2, _ = env.add_podcast("p2")
    env.followers.follow_with_gate(
        PodcastFollower(user_id=a.id, podcast_id=p1, created_at=NOW), _limit_follows(5), usage_since=DAY_AGO
    )
    env.followers.follow_with_gate(
        PodcastFollower(user_id=b.id, podcast_id=p1, created_at=NOW), _limit_follows(5), usage_since=DAY_AGO
    )
    assert env.usage.counts_by_kind(a.id, DAY_AGO) == {"follow": 1, "feed_introduced": 1}
    # Following a feed someone already follows is not "bringing it in".
    assert env.usage.counts_by_kind(b.id, DAY_AGO) == {"follow": 1}

    with pytest.raises(QuotaExceededError):
        env.followers.follow_with_gate(
            PodcastFollower(user_id=a.id, podcast_id=p2, created_at=NOW), _limit_follows(1), usage_since=DAY_AGO
        )
    assert env.followers.get_followed_podcast_ids(a.id) == [p1]
    assert env.usage.counts_by_kind(a.id, DAY_AGO)["quota_exceeded"] == 1


def test_introduced_ids_come_from_the_ledger(env):
    user = env.add_user("u@example.com")
    other = env.add_user("o@example.com")
    pid, _ = env.add_podcast("p")
    env.follow(other.id, pid, at=NOW - timedelta(days=3))
    env.follow(user.id, pid, at=NOW)
    assert env.followers.introduced_podcast_ids(user.id) == []
    # The ledger says this user brought it in (e.g. after the first
    # follower left and they followed it back from zero).
    env.usage.record(user.id, [UsageRecord("feed_introduced", pid)], now=NOW)
    assert env.followers.introduced_podcast_ids(user.id) == [pid]


def test_parallel_follows_respect_the_daily_limit(env):
    user = env.add_user("u@example.com")
    podcasts = [env.add_podcast(f"p{i}")[0] for i in range(6)]
    barrier = threading.Barrier(len(podcasts))
    outcomes = []

    def attempt(pid):
        repo = env.make_follower_repo()
        barrier.wait()
        try:
            repo.follow_with_gate(
                PodcastFollower(user_id=user.id, podcast_id=pid, created_at=NOW), _limit_follows(2), usage_since=DAY_AGO
            )
            outcomes.append("ok")
        except QuotaExceededError:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(pid,)) for pid in podcasts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes) == ["ok", "ok"] + ["refused"] * 4
    assert len(env.followers.get_followed_podcast_ids(user.id)) == 2


def test_overlapping_follows_by_several_users_do_not_deadlock(env):
    users = [env.add_user(f"u{i}@example.com") for i in range(4)]
    podcasts = [env.add_podcast(f"p{i}")[0] for i in range(4)]
    errors = []

    def run(user, order):
        repo = env.make_follower_repo()
        for pid in order:
            try:
                repo.follow_with_gate(
                    PodcastFollower(user_id=user.id, podcast_id=pid, created_at=NOW),
                    _limit_follows(100),
                    usage_since=DAY_AGO,
                )
            except Exception as exc:  # noqa: BLE001 — collected for the assertion
                errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(u, podcasts if i % 2 else list(reversed(podcasts))))
        for i, u in enumerate(users)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads), "follows deadlocked"
    assert errors == []
    assert all(env.followers.count_by_podcast(pid) == len(users) for pid in podcasts)
