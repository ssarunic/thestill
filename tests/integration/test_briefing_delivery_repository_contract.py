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

"""Dual-backend contract suite for the briefing delivery repository (spec #51).

The same tests run against BOTH the SQLite and the PostgreSQL
implementations behind the ``BriefingDeliveryRepository`` ABC — the FM-5
fidelity guarantee: the send-once constraint, the leased claim, and the
settle transitions behave identically on both engines.

The Postgres cases are skipped (not failed) when no server is reachable.
Point ``TEST_DATABASE_URL`` at a Postgres to include them:

    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55432/thestill_test \\
        ./venv/bin/python -m pytest tests/integration/test_briefing_delivery_repository_contract.py
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from thestill.models.briefing import Briefing
from thestill.models.briefing_delivery import DeliveryStatus
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_delivery_repository import SqliteBriefingDeliveryRepository
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository

PG_DSN = os.getenv("TEST_DATABASE_URL", "")

NOW = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)


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


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request, tmp_path):
    """Yield (delivery_repo, make_briefing) for each backend."""
    if request.param == "sqlite":
        db = str(tmp_path / "contract.db")
        SqlitePodcastRepository(db_path=db)  # owns the DDL
        user_repo = SqliteUserRepository(db_path=db)
        briefing_repo = SqliteBriefingRepository(db_path=db)
        delivery_repo = SqliteBriefingDeliveryRepository(db_path=db)
    else:
        if not PG_OK:
            pytest.skip("Postgres not reachable — set TEST_DATABASE_URL to include this backend")
        import psycopg

        from thestill.repositories.postgres_briefing_delivery_repository import PostgresBriefingDeliveryRepository
        from thestill.repositories.postgres_briefing_repository import PostgresBriefingRepository
        from thestill.repositories.postgres_schema import ensure_schema
        from thestill.repositories.postgres_user_repository import PostgresUserRepository

        ensure_schema(PG_DSN)
        with psycopg.connect(PG_DSN) as conn:
            conn.execute("TRUNCATE users, user_briefings, briefing_deliveries CASCADE")
        user_repo = PostgresUserRepository(PG_DSN)
        briefing_repo = PostgresBriefingRepository(PG_DSN)
        delivery_repo = PostgresBriefingDeliveryRepository(PG_DSN)

    def make_briefing() -> str:
        user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:10]}@example.com", name="u")
        user_repo.save(user)
        briefing = Briefing(
            user_id=user.id,
            cursor_from=NOW - timedelta(days=1),
            cursor_to=NOW,
            episode_count=1,
            created_at=NOW,
        )
        briefing_repo.insert(briefing)
        return briefing.id

    yield delivery_repo, make_briefing


# ---------------------------------------------------------------------------
# Send-once anchor
# ---------------------------------------------------------------------------
def test_ensure_pending_is_idempotent(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()

    assert repo.ensure_pending(briefing_id, "email", now=NOW) is True
    assert repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(hours=1)) is False

    delivery = repo.get_for_briefing(briefing_id, "email")
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.attempts == 0
    assert delivery.next_attempt_at == NOW  # first writer wins


def test_settled_delivery_is_never_reopened(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    repo.mark_sent(repo.get_for_briefing(briefing_id, "email").id, sent_at=NOW)

    assert repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(days=1)) is False
    assert repo.get_for_briefing(briefing_id, "email").status == DeliveryStatus.SENT


# ---------------------------------------------------------------------------
# Due-scan + claim
# ---------------------------------------------------------------------------
def test_due_scan_orders_oldest_first_and_skips_terminal(backend):
    repo, make_briefing = backend
    late = make_briefing()
    repo.ensure_pending(late, "email", now=NOW + timedelta(minutes=5))
    early = make_briefing()
    repo.ensure_pending(early, "email", now=NOW)
    sent = make_briefing()
    repo.ensure_pending(sent, "email", now=NOW)
    repo.mark_sent(repo.get_for_briefing(sent, "email").id, sent_at=NOW)

    due = repo.due(NOW + timedelta(minutes=10), limit=10)
    assert [d.briefing_id for d in due] == [early, late]


def test_claim_contention_single_winner(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    delivery = repo.get_for_briefing(briefing_id, "email")

    outcomes = [repo.claim(delivery.id, now=NOW, lease_seconds=600) for _ in range(3)]
    assert outcomes == [True, False, False]

    claimed = repo.get_for_briefing(briefing_id, "email")
    assert claimed.status == DeliveryStatus.SENDING
    assert claimed.next_attempt_at == NOW + timedelta(seconds=600)


def test_expired_lease_is_reclaimable(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    delivery = repo.get_for_briefing(briefing_id, "email")
    assert repo.claim(delivery.id, now=NOW, lease_seconds=600)

    after_lease = NOW + timedelta(seconds=601)
    assert [d.id for d in repo.due(after_lease, limit=10)] == [delivery.id]
    assert repo.claim(delivery.id, now=after_lease, lease_seconds=600) is True


def test_claim_increments_attempts(backend):
    # Budget burns at claim time, so a crash between claim and settle
    # still counts against max_attempts on every backend.
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    delivery = repo.get_for_briefing(briefing_id, "email")

    assert repo.claim(delivery.id, now=NOW, lease_seconds=600)
    assert repo.get_for_briefing(briefing_id, "email").attempts == 1

    assert repo.claim(delivery.id, now=NOW + timedelta(seconds=601), lease_seconds=600)
    assert repo.get_for_briefing(briefing_id, "email").attempts == 2


# ---------------------------------------------------------------------------
# Settle transitions
# ---------------------------------------------------------------------------
def test_retry_then_sent_round_trip(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    delivery = repo.get_for_briefing(briefing_id, "email")

    repo.claim(delivery.id, now=NOW, lease_seconds=600)
    retry_at = NOW + timedelta(seconds=300)
    repo.mark_retry(delivery.id, attempts=1, next_attempt_at=retry_at, error="relay down")

    row = repo.get_for_briefing(briefing_id, "email")
    assert (row.status, row.attempts, row.next_attempt_at, row.last_error) == (
        DeliveryStatus.PENDING,
        1,
        retry_at,
        "relay down",
    )

    repo.claim(delivery.id, now=retry_at, lease_seconds=600)
    repo.mark_sent(delivery.id, sent_at=retry_at)
    row = repo.get_for_briefing(briefing_id, "email")
    assert row.status == DeliveryStatus.SENT
    assert row.sent_at == retry_at
    assert row.next_attempt_at is None
    assert row.last_error is None


def test_failed_parks_out_of_scan(backend):
    repo, make_briefing = backend
    briefing_id = make_briefing()
    repo.ensure_pending(briefing_id, "email", now=NOW)
    delivery = repo.get_for_briefing(briefing_id, "email")
    repo.mark_failed(delivery.id, attempts=3, error="relay down")

    row = repo.get_for_briefing(briefing_id, "email")
    assert row.status == DeliveryStatus.FAILED
    assert row.next_attempt_at is None
    assert repo.due(NOW + timedelta(days=365), limit=10) == []
