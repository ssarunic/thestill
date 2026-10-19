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

"""Dual-backend contract suite for what the spec #104 Phase 3 alerts read:
the atomic alert claim, per-user ledger counts, recent signups and the
audio queued for transcription."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from tests.integration.test_usage_ledger_contract import NOW, PG_DSN, PG_OK, _Postgres, _Sqlite
from thestill.core.queue_manager import TaskStage
from thestill.models.user import User
from thestill.repositories.usage_event_repository import UsageRecord


@pytest.fixture(params=["sqlite", "postgres"])
def env(request, tmp_path):
    if request.param == "sqlite":
        return _Sqlite(str(tmp_path / "alerts.db"))
    if not PG_OK:
        pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
    return _Postgres(PG_DSN)


def test_claim_wins_once_per_interval(env):
    key = "alerts.last_sent.queue_backlog"
    assert env.settings.claim(key, {"title": "a"}, now=NOW, min_interval_seconds=3600) is True
    assert env.settings.claim(key, {"title": "b"}, now=NOW + timedelta(minutes=59), min_interval_seconds=3600) is False
    assert env.settings.get_all()[key].value == {"title": "a"}
    assert env.settings.claim(key, {"title": "c"}, now=NOW + timedelta(hours=1), min_interval_seconds=3600) is True
    assert env.settings.get_all()[key].value == {"title": "c"}


def test_concurrent_claims_have_one_winner(env):
    n = 6
    barrier = threading.Barrier(n)
    wins = []

    def attempt():
        repo = type(env.settings)(env.db_path if env.backend == "sqlite" else env.dsn)
        barrier.wait()
        wins.append(repo.claim("alerts.digest", {"date": "x"}, now=NOW, min_interval_seconds=3600))

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert wins.count(True) == 1


def test_counts_by_user_and_created_since(env):
    a, b = env.add_user("a@example.com"), env.add_user("b@example.com")
    env.usage.record(a.id, [UsageRecord("quota_exceeded", "q")] * 3, now=NOW)
    env.usage.record(b.id, [UsageRecord("quota_exceeded", "q")], now=NOW - timedelta(hours=2))
    env.usage.record(b.id, [UsageRecord("feed_introduced", "p")], now=NOW)
    assert env.usage.counts_by_user("quota_exceeded", NOW - timedelta(hours=1)) == {a.id: 3}
    assert env.usage.counts_by_user("feed_introduced", NOW - timedelta(hours=1)) == {b.id: 1}

    env.users.save(User(email="new@example.com", created_at=NOW + timedelta(minutes=5)))
    recent = env.directory.created_since(NOW)
    assert recent[0].email == "new@example.com"  # newest first
    assert {u.email for u in recent[1:]} == {"a@example.com", "b@example.com"}
    assert env.directory.created_since(NOW + timedelta(minutes=1))[0].email == "new@example.com"


def test_queued_audio_durations_one_per_episode(env):
    _, episodes = env.add_podcast("show", episodes=2)
    env.queue.add_task(episodes[0], TaskStage.DOWNLOAD)
    env.queue.add_task(episodes[0], TaskStage.TRANSCRIBE)
    env.queue.add_task(episodes[1], TaskStage.SUMMARIZE)  # not transcription
    durations = env.queue.queued_audio_durations(NOW - timedelta(days=365))
    assert durations == ["60"]
