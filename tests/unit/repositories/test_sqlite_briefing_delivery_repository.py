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

"""Unit tests for ``SqliteBriefingDeliveryRepository`` (spec #51).

The send-once anchor (ensure_pending under racing triggers), the due-scan
(pending + lease-expired sending rows), claim contention, and the three
settle transitions.
"""

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

NOW = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "deliveries.db"
    SqlitePodcastRepository(str(path))
    return str(path)


@pytest.fixture
def repo(db_path):
    return SqliteBriefingDeliveryRepository(db_path)


@pytest.fixture
def briefing_id(db_path):
    """A persisted user + briefing to satisfy the FK chain."""
    user = User(id=str(uuid.uuid4()), email="alice@example.com", name="alice")
    SqliteUserRepository(db_path).save(user)
    briefing = Briefing(
        user_id=user.id,
        cursor_from=NOW - timedelta(days=1),
        cursor_to=NOW,
        episode_count=3,
        created_at=NOW,
    )
    SqliteBriefingRepository(db_path).insert(briefing)
    return briefing.id


class TestEnsurePending:
    def test_creates_pending_row(self, repo, briefing_id):
        assert repo.ensure_pending(briefing_id, "email", now=NOW) is True

        delivery = repo.get_for_briefing(briefing_id, "email")
        assert delivery is not None
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == 0
        assert delivery.next_attempt_at == NOW

    def test_racing_triggers_collapse_to_one_row(self, repo, briefing_id):
        # The lazy-then-scheduled interaction: both triggers ensure, the
        # UNIQUE constraint keeps exactly one delivery.
        assert repo.ensure_pending(briefing_id, "email", now=NOW) is True
        assert repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(minutes=30)) is False

        delivery = repo.get_for_briefing(briefing_id, "email")
        assert delivery.next_attempt_at == NOW  # first writer wins

    def test_never_reopens_a_settled_delivery(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        repo.mark_sent(delivery.id, sent_at=NOW)

        assert repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(hours=1)) is False
        assert repo.get_for_briefing(briefing_id, "email").status == DeliveryStatus.SENT


class TestDueScan:
    def test_returns_due_pending_oldest_first(self, repo, db_path):
        ids = []
        for offset in (2, 0, 1):
            briefing_id = _make_briefing(db_path, created_at=NOW + timedelta(minutes=offset))
            repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(minutes=offset))
            ids.append((offset, briefing_id))

        due = repo.due(NOW + timedelta(minutes=5), limit=10)
        assert [d.briefing_id for d in due] == [bid for _, bid in sorted(ids)]

    def test_excludes_future_and_terminal_rows(self, repo, db_path):
        future_id = _make_briefing(db_path)
        repo.ensure_pending(future_id, "email", now=NOW + timedelta(hours=1))
        sent_id = _make_briefing(db_path)
        repo.ensure_pending(sent_id, "email", now=NOW)
        repo.mark_sent(repo.get_for_briefing(sent_id, "email").id, sent_at=NOW)
        failed_id = _make_briefing(db_path)
        repo.ensure_pending(failed_id, "email", now=NOW)
        repo.mark_failed(repo.get_for_briefing(failed_id, "email").id, attempts=3, error="smtp down")

        assert repo.due(NOW, limit=10) == []

    def test_includes_sending_row_with_expired_lease(self, repo, briefing_id):
        # Crash-mid-send recovery: a 'sending' row becomes claimable again
        # once its lease (next_attempt_at) passes.
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        assert repo.claim(delivery.id, now=NOW, lease_seconds=600)

        assert repo.due(NOW + timedelta(seconds=599), limit=10) == []
        recovered = repo.due(NOW + timedelta(seconds=601), limit=10)
        assert [d.id for d in recovered] == [delivery.id]
        assert recovered[0].status == DeliveryStatus.SENDING


class TestClaim:
    def test_claim_moves_to_sending_with_lease(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")

        assert repo.claim(delivery.id, now=NOW, lease_seconds=600) is True

        claimed = repo.get_for_briefing(briefing_id, "email")
        assert claimed.status == DeliveryStatus.SENDING
        assert claimed.next_attempt_at == NOW + timedelta(seconds=600)

    def test_claim_increments_attempts(self, repo, briefing_id):
        # Budget burns at claim time — a crash before settling must not
        # reset the counter, or a crash-looping send retries forever.
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")

        repo.claim(delivery.id, now=NOW, lease_seconds=600)

        assert repo.get_for_briefing(briefing_id, "email").attempts == 1

    def test_second_claim_within_lease_loses(self, repo, briefing_id):
        # Multi-instance contention: only one delivery pass wins the row.
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")

        assert repo.claim(delivery.id, now=NOW, lease_seconds=600) is True
        assert repo.claim(delivery.id, now=NOW + timedelta(seconds=1), lease_seconds=600) is False

    def test_cannot_claim_before_due(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW + timedelta(minutes=10))
        delivery = repo.get_for_briefing(briefing_id, "email")

        assert repo.claim(delivery.id, now=NOW, lease_seconds=600) is False

    def test_cannot_claim_terminal_row(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        repo.mark_sent(delivery.id, sent_at=NOW)

        assert repo.claim(delivery.id, now=NOW + timedelta(hours=1), lease_seconds=600) is False


class TestSettle:
    def test_mark_sent_is_terminal_and_clears_error(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        repo.mark_retry(delivery.id, attempts=1, next_attempt_at=NOW, error="blip")
        repo.mark_sent(delivery.id, sent_at=NOW + timedelta(minutes=5))

        settled = repo.get_for_briefing(briefing_id, "email")
        assert settled.status == DeliveryStatus.SENT
        assert settled.sent_at == NOW + timedelta(minutes=5)
        assert settled.next_attempt_at is None
        assert settled.last_error is None

    def test_mark_retry_requeues_with_backoff_time(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        repo.claim(delivery.id, now=NOW, lease_seconds=600)
        retry_at = NOW + timedelta(seconds=300)
        repo.mark_retry(delivery.id, attempts=1, next_attempt_at=retry_at, error="smtp down")

        settled = repo.get_for_briefing(briefing_id, "email")
        assert settled.status == DeliveryStatus.PENDING
        assert settled.attempts == 1
        assert settled.next_attempt_at == retry_at
        assert settled.last_error == "smtp down"

    def test_mark_failed_parks_out_of_due_scan(self, repo, briefing_id):
        repo.ensure_pending(briefing_id, "email", now=NOW)
        delivery = repo.get_for_briefing(briefing_id, "email")
        repo.mark_failed(delivery.id, attempts=3, error="smtp down")

        settled = repo.get_for_briefing(briefing_id, "email")
        assert settled.status == DeliveryStatus.FAILED
        assert settled.attempts == 3
        assert settled.next_attempt_at is None
        assert repo.due(NOW + timedelta(days=365), limit=10) == []


def _make_briefing(db_path: str, created_at: datetime = NOW) -> str:
    user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}@example.com", name="u")
    SqliteUserRepository(db_path).save(user)
    briefing = Briefing(
        user_id=user.id,
        cursor_from=created_at - timedelta(days=1),
        cursor_to=created_at,
        episode_count=1,
        created_at=created_at,
    )
    SqliteBriefingRepository(db_path).insert(briefing)
    return briefing.id
