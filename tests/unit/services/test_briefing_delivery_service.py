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

"""Unit tests for ``BriefingDeliveryService`` (spec #51).

Send-once semantics, the claim → render → send → settle pass, bounded
exponential backoff, parking after ``max_attempts``, permanent-failure
short-circuits (opt-out at send time, missing user/script), and recipient
resolution at send time. Real SQLite repositories, fake sender.
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from thestill.models.briefing import Briefing
from thestill.models.briefing_delivery import DeliveryStatus
from thestill.models.briefing_schedule import BriefingSchedule
from thestill.models.user import User
from thestill.repositories.sqlite_briefing_delivery_repository import SqliteBriefingDeliveryRepository
from thestill.repositories.sqlite_briefing_repository import SqliteBriefingRepository
from thestill.repositories.sqlite_briefing_schedule_repository import SqliteBriefingScheduleRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.briefing_delivery_service import BriefingDeliveryService
from thestill.services.briefing_email_renderer import BriefingEmailRenderer
from thestill.services.email_sender import EmailSender, EmailSendError
from thestill.utils.file_storage import LocalFileStorage, StorageError
from thestill.utils.path_manager import PathManager

NOW = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)


class FakeSender(EmailSender):
    """Captures sends; raises for a configurable number of attempts."""

    def __init__(self, fail_times: int = 0):
        self.sent = []
        self.fail_times = fail_times

    def send(self, *, to, subject, html, text, headers=None):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise EmailSendError("SMTP send failed: relay down")
        self.sent.append({"to": to, "subject": subject, "html": html, "text": text, "headers": headers or {}})


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "delivery-service.db"
    SqlitePodcastRepository(str(path))
    return str(path)


@pytest.fixture
def repos(db_path):
    return {
        "delivery": SqliteBriefingDeliveryRepository(db_path),
        "briefing": SqliteBriefingRepository(db_path),
        "schedule": SqliteBriefingScheduleRepository(db_path),
        "user": SqliteUserRepository(db_path),
    }


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def service(repos, sender, tmp_path):
    return _make_service(repos, sender, tmp_path)


def _make_service(repos, sender, tmp_path, **overrides) -> BriefingDeliveryService:
    kwargs = {
        "max_attempts": 3,
        "backoff_seconds": 300,
        # Scripts are read back through FileStorage (spec #35) — root both
        # at tmp_path, where _seed_user writes them.
        "path_manager": PathManager(str(tmp_path)),
        "file_storage": LocalFileStorage(str(tmp_path)),
    }
    kwargs.update(overrides)
    return BriefingDeliveryService(
        repos["delivery"],
        repos["briefing"],
        repos["schedule"],
        repos["user"],
        BriefingEmailRenderer(public_base_url="https://app.example.com", secret="test-secret"),
        sender,
        **kwargs,
    )


def _seed_user(repos, tmp_path, *, email_enabled=True, script=True, email="alice@example.com") -> Briefing:
    user = User(id=str(uuid.uuid4()), email=email, name="alice")
    repos["user"].save(user)
    repos["schedule"].upsert(
        BriefingSchedule(
            user_id=user.id,
            timezone_name="Europe/Zagreb",
            enabled=True,
            email_enabled=email_enabled,
            next_run_at=NOW + timedelta(days=1),
        )
    )
    script_path: Optional[str] = None
    if script:
        path = tmp_path / f"script-{user.id}.md"
        path.write_text(
            "# Morning Briefing\n\n## 🎙️ Some Show (1 episode)\n\n"
            "### [Ep 1](/podcasts/some-show/episodes/ep-1)\n\nA **great** episode.\n",
            encoding="utf-8",
        )
        script_path = str(path)
    briefing = Briefing(
        user_id=user.id,
        cursor_from=NOW - timedelta(days=1),
        cursor_to=NOW,
        episode_count=1,
        script_path=script_path,
        created_at=NOW,
    )
    repos["briefing"].insert(briefing)
    return briefing


class TestSendOnce:
    def test_pending_delivery_sends_exactly_once(self, service, repos, sender, tmp_path):
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 1
        assert service.deliver_due(now=NOW + timedelta(minutes=1)) == 0
        assert len(sender.sent) == 1
        assert sender.sent[0]["to"] == "alice@example.com"

    def test_racing_ensure_pending_still_sends_once(self, service, repos, sender, tmp_path):
        # Lazy-open trigger and scheduled slot both ensure the same
        # briefing: the constraint collapses them to one send.
        briefing = _seed_user(repos, tmp_path)
        assert service.ensure_pending(briefing.id, now=NOW) is True
        assert service.ensure_pending(briefing.id, now=NOW + timedelta(minutes=30)) is False

        assert service.deliver_due(now=NOW + timedelta(minutes=31)) == 1
        assert len(sender.sent) == 1

    def test_ensure_pending_after_sent_does_not_requeue(self, service, repos, sender, tmp_path):
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)
        service.deliver_due(now=NOW)

        assert service.ensure_pending(briefing.id, now=NOW + timedelta(hours=2)) is False
        assert service.deliver_due(now=NOW + timedelta(hours=2)) == 0
        assert len(sender.sent) == 1


class TestEmailContent:
    def test_email_carries_absolute_links_and_unsubscribe(self, service, repos, sender, tmp_path):
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)
        service.deliver_due(now=NOW)

        [email] = sender.sent
        assert "https://app.example.com/podcasts/some-show/episodes/ep-1" in email["html"]
        assert "https://app.example.com/podcasts/some-show/episodes/ep-1" in email["text"]
        assert "https://app.example.com/unsubscribe/briefings?token=" in email["text"]
        assert email["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert email["headers"]["List-Unsubscribe"].startswith("<https://app.example.com/unsubscribe/")
        assert "1 new episode" in email["subject"]

    def test_recipient_resolved_at_send_time(self, service, repos, sender, tmp_path, db_path):
        # Address change between generation and send goes to the current
        # address — recipient is never denormalized onto the delivery.
        import sqlite3

        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ?",
                ("new-address@example.com", briefing.user_id),
            )

        service.deliver_due(now=NOW)
        assert sender.sent[0]["to"] == "new-address@example.com"


class TestRetryAndParking:
    def test_failed_send_backs_off_exponentially(self, repos, tmp_path):
        sender = FakeSender(fail_times=2)
        service = _make_service(repos, sender, tmp_path)
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == 1
        assert delivery.next_attempt_at == NOW + timedelta(seconds=300)

        # Not due yet — nothing happens.
        assert service.deliver_due(now=NOW + timedelta(seconds=299)) == 0

        second = NOW + timedelta(seconds=300)
        assert service.deliver_due(now=second) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.attempts == 2
        assert delivery.next_attempt_at == second + timedelta(seconds=600)  # doubled

        third = second + timedelta(seconds=600)
        assert service.deliver_due(now=third) == 1
        assert repos["delivery"].get_for_briefing(briefing.id, "email").status == DeliveryStatus.SENT
        assert len(sender.sent) == 1

    def test_parks_failed_after_max_attempts(self, repos, tmp_path):
        sender = FakeSender(fail_times=99)
        service = _make_service(repos, sender, tmp_path)
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)

        now = NOW
        for _ in range(3):
            service.deliver_due(now=now)
            now += timedelta(hours=1)

        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.attempts == 3
        assert "relay down" in delivery.last_error
        # Parked: never claimed again (FM-1 — doesn't burn every tick).
        assert service.deliver_due(now=now) == 0

    def test_crash_looping_send_parks_after_budget(self, repos, tmp_path):
        # A process crash between claim and settle (claimed rows that
        # never settled) must still burn retry budget — otherwise a send
        # that reproducibly kills the worker re-claims forever.
        sender = FakeSender()
        service = _make_service(repos, sender, tmp_path)
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")

        now = NOW
        for _ in range(3):
            assert repos["delivery"].claim(delivery.id, now=now, lease_seconds=600)
            now += timedelta(seconds=601)  # lease expires without a settle

        assert service.deliver_due(now=now) == 0

        parked = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert parked.status == DeliveryStatus.FAILED
        assert sender.sent == []

    def test_one_failing_delivery_does_not_block_others(self, repos, tmp_path):
        # FM-1 per-delivery isolation inside a single pass.
        failing = _seed_user(repos, tmp_path, script=False)  # permanent failure
        healthy = _seed_user(repos, tmp_path, email="bob@example.com")
        sender = FakeSender()
        service = _make_service(repos, sender, tmp_path)
        service.ensure_pending(failing.id, now=NOW)
        service.ensure_pending(healthy.id, now=NOW + timedelta(seconds=1))

        assert service.deliver_due(now=NOW + timedelta(seconds=2)) == 1
        assert sender.sent[0]["to"] == "bob@example.com"
        assert repos["delivery"].get_for_briefing(failing.id, "email").status == DeliveryStatus.FAILED


class TestPermanentFailures:
    def test_unsubscribe_between_queue_and_send_wins(self, service, repos, sender, tmp_path):
        # The 7:59-unsubscribe / 8:00-send race: opt-out is re-checked at
        # send time, so the pending row parks instead of sending.
        briefing = _seed_user(repos, tmp_path)
        service.ensure_pending(briefing.id, now=NOW)
        repos["schedule"].set_email_enabled(briefing.user_id, False)

        assert service.deliver_due(now=NOW) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.FAILED
        assert "disabled" in delivery.last_error
        assert sender.sent == []

    def test_missing_script_parks_immediately(self, service, repos, sender, tmp_path):
        briefing = _seed_user(repos, tmp_path, script=False)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.FAILED
        assert sender.sent == []

    def test_user_without_schedule_parks(self, service, repos, sender, tmp_path):
        # No schedule row at all — "no schedule, no sends".
        user = User(id=str(uuid.uuid4()), email="ghost@example.com", name="ghost")
        repos["user"].save(user)
        briefing = Briefing(
            user_id=user.id,
            cursor_from=NOW - timedelta(days=1),
            cursor_to=NOW,
            episode_count=1,
            created_at=NOW,
        )
        repos["briefing"].insert(briefing)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 0
        assert repos["delivery"].get_for_briefing(briefing.id, "email").status == DeliveryStatus.FAILED


class TestStorageBackedScripts:
    """Spec #35 × #51: scripts are written through FileStorage, so the
    delivery pass must read them the same way — with STORAGE_BACKEND=s3
    the absolute path never exists on local disk."""

    def test_sends_when_script_exists_only_in_storage(self, repos, tmp_path):
        sender = FakeSender()
        briefing = _seed_user(repos, tmp_path)
        content = Path(briefing.script_path).read_text(encoding="utf-8")
        Path(briefing.script_path).unlink()  # S3-like: nothing on local disk
        storage = MagicMock()
        storage.read_text.return_value = content
        service = _make_service(repos, sender, tmp_path, file_storage=storage)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 1
        storage.read_text.assert_called_once_with(f"script-{briefing.user_id}.md")
        assert len(sender.sent) == 1

    def test_missing_storage_object_parks_permanently(self, repos, tmp_path):
        sender = FakeSender()
        briefing = _seed_user(repos, tmp_path)
        storage = MagicMock()
        storage.read_text.side_effect = FileNotFoundError("gone")
        service = _make_service(repos, sender, tmp_path, file_storage=storage)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.FAILED
        assert sender.sent == []

    def test_transient_storage_error_retries(self, repos, tmp_path):
        # An S3 flake is retryable — it must back off, not park.
        sender = FakeSender()
        briefing = _seed_user(repos, tmp_path)
        storage = MagicMock()
        storage.read_text.side_effect = StorageError("s3 get_object failed: throttled")
        service = _make_service(repos, sender, tmp_path, file_storage=storage)
        service.ensure_pending(briefing.id, now=NOW)

        assert service.deliver_due(now=NOW) == 0
        delivery = repos["delivery"].get_for_briefing(briefing.id, "email")
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == 1
        assert delivery.next_attempt_at == NOW + timedelta(seconds=300)
