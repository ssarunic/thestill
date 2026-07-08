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

"""
Briefing delivery service (spec #51).

Owns the delivery state machine: ``ensure_pending`` (the send-once rule,
anchored on the repository's UNIQUE constraint) and ``deliver_due`` (claim
→ render → send → settle, with bounded exponential backoff). Decoupled
from generation by design — a briefing is emailed because *it hasn't been
emailed yet*, never because it was just generated, which is what keeps the
lazy-open-then-scheduled-slot interaction at exactly one email.

Owns no SMTP details (``EmailSender``) and no markup (``BriefingEmailRenderer``).
The recipient address is resolved from ``users`` at send time, not
denormalized — an address change between generation and a retry goes to
the current address.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ..models.briefing_delivery import BriefingDelivery, DeliveryChannel

if TYPE_CHECKING:
    from ..repositories.briefing_delivery_repository import BriefingDeliveryRepository
    from ..repositories.briefing_repository import BriefingRepository
    from ..repositories.briefing_schedule_repository import BriefingScheduleRepository
    from ..repositories.user_repository import UserRepository
    from .briefing_email_renderer import BriefingEmailRenderer
    from .email_sender import EmailSender

logger = get_logger(__name__)


class _PermanentDeliveryError(Exception):
    """A failure no retry can fix (missing user/script, delivery opted
    out). Parks the row immediately instead of burning the retry budget."""


class BriefingDeliveryService:
    """Claim → render → send → settle for pending briefing deliveries."""

    def __init__(
        self,
        delivery_repository: "BriefingDeliveryRepository",
        briefing_repository: "BriefingRepository",
        schedule_repository: "BriefingScheduleRepository",
        user_repository: "UserRepository",
        renderer: "BriefingEmailRenderer",
        sender: "EmailSender",
        *,
        max_attempts: int = 3,
        backoff_seconds: int = 300,
        max_per_pass: int = 50,
        claim_lease_seconds: int = 600,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        self._deliveries = delivery_repository
        self._briefings = briefing_repository
        self._schedules = schedule_repository
        self._users = user_repository
        self._renderer = renderer
        self._sender = sender
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_per_pass = max_per_pass
        self._claim_lease = claim_lease_seconds
        logger.info(
            "BriefingDeliveryService initialized",
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )

    def ensure_pending(
        self,
        briefing_id: str,
        channel: DeliveryChannel = DeliveryChannel.EMAIL,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        """Guarantee a delivery row exists for ``(briefing_id, channel)``.

        Idempotent under racing triggers (constraint-level ``ON CONFLICT DO
        NOTHING``): a briefing that already has a delivery — pending, sent,
        or failed — is never re-queued. Returns True when a new pending row
        was created.
        """
        clock_now = now or datetime.now(timezone.utc)
        created = self._deliveries.ensure_pending(briefing_id, channel.value, now=clock_now)
        if created:
            logger.info("briefing_delivery_queued", briefing_id=briefing_id, channel=channel.value)
        return created

    def deliver_due(self, now: Optional[datetime] = None) -> int:
        """One delivery pass: send every claimable delivery, oldest first.

        Per-delivery isolation (FM-1): one failed send settles its own row
        (retry with backoff, or parked ``failed`` after ``max_attempts``)
        and never blocks the rest of the pass. Returns the number sent.
        """
        clock_now = now or datetime.now(timezone.utc)
        due = self._deliveries.due(clock_now, limit=self._max_per_pass)
        sent = 0
        for delivery in due:
            if not self._deliveries.claim(delivery.id, now=clock_now, lease_seconds=self._claim_lease):
                # Another instance took it, or it settled mid-scan.
                continue
            attempts = delivery.attempts + 1
            try:
                self._send_one(delivery)
            except _PermanentDeliveryError as exc:
                self._deliveries.mark_failed(delivery.id, attempts=attempts, error=str(exc))
                logger.warning(
                    "briefing_delivery_parked",
                    delivery_id=delivery.id,
                    briefing_id=delivery.briefing_id,
                    attempts=attempts,
                    error=str(exc),
                )
            except Exception as exc:
                self._settle_retryable(delivery, attempts, str(exc), clock_now)
            else:
                self._deliveries.mark_sent(delivery.id, sent_at=clock_now)
                sent += 1
                logger.info(
                    "briefing_delivery_sent",
                    delivery_id=delivery.id,
                    briefing_id=delivery.briefing_id,
                    attempts=attempts,
                )
        if due:
            logger.info("briefing_delivery_pass", due=len(due), sent=sent)
        return sent

    def _send_one(self, delivery: BriefingDelivery) -> None:
        """Render and send one claimed delivery.

        Raises ``_PermanentDeliveryError`` for states no retry fixes;
        anything else (transport errors included) is retryable.
        """
        briefing = self._briefings.get(delivery.briefing_id)
        if briefing is None:
            raise _PermanentDeliveryError("briefing row no longer exists")

        # Re-check the opt-in at send time: an unsubscribe between queueing
        # and sending must win (the 7:59-unsubscribe / 8:00-send race).
        schedule = self._schedules.get(briefing.user_id)
        if schedule is None or not (schedule.enabled and schedule.email_enabled):
            raise _PermanentDeliveryError("email delivery disabled for user")

        user = self._users.get_by_id(briefing.user_id)
        if user is None or not user.email:
            raise _PermanentDeliveryError("user missing or has no email address")

        if not briefing.script_path:
            raise _PermanentDeliveryError("briefing has no rendered script")
        script_path = Path(briefing.script_path)
        if not script_path.exists():
            raise _PermanentDeliveryError(f"briefing script missing on disk: {script_path}")

        email = self._renderer.render(briefing, script_path.read_text(encoding="utf-8"))
        self._sender.send(
            to=user.email,
            subject=email.subject,
            html=email.html,
            text=email.text,
            headers=email.headers,
        )

    def _settle_retryable(self, delivery: BriefingDelivery, attempts: int, error: str, now: datetime) -> None:
        if attempts >= self._max_attempts:
            self._deliveries.mark_failed(delivery.id, attempts=attempts, error=error)
            logger.error(
                "briefing_delivery_failed",
                delivery_id=delivery.id,
                briefing_id=delivery.briefing_id,
                attempts=attempts,
                error=error,
            )
            return
        # Exponential backoff: backoff, 2×backoff, 4×backoff, …
        next_attempt_at = now + timedelta(seconds=self._backoff * (2 ** (attempts - 1)))
        self._deliveries.mark_retry(delivery.id, attempts=attempts, next_attempt_at=next_attempt_at, error=error)
        logger.warning(
            "briefing_delivery_retry_scheduled",
            delivery_id=delivery.id,
            briefing_id=delivery.briefing_id,
            attempts=attempts,
            next_attempt_at=next_attempt_at.isoformat(),
            error=error,
        )
