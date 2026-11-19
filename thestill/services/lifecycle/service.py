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

"""Welcome and product-tips emails (spec #118).

One periodic ``tick`` does two things:

1. **Plan.** Read ``LifecycleFacts`` for recent signups who have "Product
   tips by email" on, pick at most one due message per user, and queue it
   (``ensure_pending``: the ledger's UNIQUE constraint makes a message
   send-once however many workers plan it).
2. **Deliver.** Claim → re-check → render → send → settle, the spec #51
   state machine with bounded exponential backoff.

The rules a message must pass, at plan time and again at send time:

- the account is active and tips are on (the facts query enforces both);
- the message is due (``send_after``) and not expired (plan time only);
- its condition still holds (a reader who followed a show since is not
  told to follow one);
- frequency: no lifecycle message in the last ``min_gap``, and no briefing
  email in the last ``briefing_quiet`` (one email a day, not two);
- it is daytime for the reader (``send_window``, in the briefing
  schedule's time zone, else UTC).

Nothing here is triggered by an event, so restarts, deploys and analytics
opt-outs can't lose or skip a message: the next tick sees the same facts.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

from structlog import get_logger

from ...models.user_message import LifecycleFacts, MessageChannel, UserMessage
from ..email_sender import EmailSender
from .messages import SEQUENCE, LifecycleMessage
from .renderer import LifecycleEmailRenderer

logger = get_logger(__name__)

_CHANNEL = MessageChannel.EMAIL.value


class _Skip(Exception):
    """The message no longer applies; settle it as ``skipped``."""


class _Defer(Exception):
    """Not now (outside the reader's hours, or a briefing email just went
    out): put the row back, due at ``until``, without spending an attempt."""

    def __init__(self, until: datetime, reason: str) -> None:
        super().__init__(reason)
        self.until = until


class LifecycleEmailService:
    def __init__(
        self,
        repository,
        renderer: LifecycleEmailRenderer,
        sender: EmailSender,
        *,
        enabled: Callable[[], bool] = lambda: True,
        sequence: Tuple[LifecycleMessage, ...] = SEQUENCE,
        min_gap: timedelta = timedelta(days=2),
        briefing_quiet: timedelta = timedelta(hours=12),
        send_window: Tuple[int, int] = (9, 19),
        max_attempts: int = 3,
        backoff_seconds: int = 300,
        max_per_pass: int = 50,
        page_size: int = 500,
        claim_lease_seconds: int = 600,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not sequence:
            raise ValueError("the lifecycle sequence is empty")
        self._repo = repository
        self._renderer = renderer
        self._sender = sender
        self._enabled = enabled
        self._sequence = tuple(sorted(sequence, key=lambda m: m.send_after))
        self._by_key: Dict[str, LifecycleMessage] = {m.key: m for m in self._sequence}
        self._min_gap = min_gap
        self._briefing_quiet = briefing_quiet
        self._window = send_window
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._max_per_pass = max_per_pass
        if page_size < 1:
            raise ValueError("page_size must be at least 1")
        self._page_size = page_size
        self._lease = claim_lease_seconds

    @property
    def sequence(self) -> Tuple[LifecycleMessage, ...]:
        return self._sequence

    def message(self, key: str) -> LifecycleMessage:
        return self._by_key[key]

    # ------------------------------------------------------------------ plan

    def tick(self, now: Optional[datetime] = None) -> None:
        """One pass (the periodic job). Does nothing while the
        ``lifecycle_email_enabled`` switch is off."""
        if not self._enabled():
            return
        self.plan(now)
        self.deliver_due(now)

    def candidates(self, now: datetime) -> Iterator[LifecycleFacts]:
        """Facts for everyone the sequence could still reach at ``now``,
        page by page (keyset), so no signup is starved by older ones."""
        earliest = min(m.send_after for m in self._sequence)
        latest = max(m.expires_after for m in self._sequence)
        after: Optional[Tuple[datetime, str]] = None
        while True:
            page = self._repo.candidates(
                created_from=now - latest,
                created_to=now - earliest,
                channel=_CHANNEL,
                limit=self._page_size,
                after=after,
            )
            yield from page
            if len(page) < self._page_size:
                return
            after = (page[-1].created_at, page[-1].user_id)

    def next_message(self, facts: LifecycleFacts, now: datetime) -> Tuple[Optional[LifecycleMessage], str]:
        """The message to queue for ``facts`` now, or None with the reason.

        Pure: the planner and ``thestill lifecycle plan`` both use it."""
        if not self._in_send_window(facts, now):
            return None, "outside send window"
        if facts.last_message_at is not None and now - facts.last_message_at < self._min_gap:
            return None, "frequency cap"
        if facts.last_briefing_email_at is not None and now - facts.last_briefing_email_at < self._briefing_quiet:
            return None, "briefing email sent recently"
        age = now - facts.created_at
        for message in self._sequence:
            if message.key in facts.message_keys:
                continue
            if age < message.send_after:
                return None, "nothing due yet"
            if age >= message.expires_after:
                continue
            if not message.applies(facts):
                continue
            return message, "due"
        return None, "nothing applies"

    def plan(self, now: Optional[datetime] = None) -> int:
        """Queue at most one due message per candidate. Returns how many
        rows were created (a row another worker created first counts 0)."""
        clock_now = now or datetime.now(timezone.utc)
        queued = 0
        for facts in self.candidates(clock_now):
            message, _reason = self.next_message(facts, clock_now)
            if message is None:
                continue
            if self._repo.ensure_pending(facts.user_id, message.key, _CHANNEL, now=clock_now):
                queued += 1
                logger.info("lifecycle_message_queued", user_id=facts.user_id, message_key=message.key)
        return queued

    # --------------------------------------------------------------- deliver

    def deliver_due(self, now: Optional[datetime] = None) -> int:
        """Send every claimable row. Each row settles on its own (FM-1):
        a failure retries with backoff or parks, never blocks the pass."""
        clock_now = now or datetime.now(timezone.utc)
        due = self._repo.due(clock_now, limit=self._max_per_pass)
        sent = 0
        for row in due:
            if row.attempts >= self._max_attempts:
                # A claim spends an attempt, so a row at the budget with an
                # unsettled claim crashed mid-send on its last try: park it.
                error = row.last_error or "send did not settle; retry budget exhausted"
                self._repo.mark_failed(row.id, attempts=row.attempts, error=error)
                logger.error("lifecycle_message_failed", message_id=row.id, message_key=row.message_key, error=error)
                continue
            claim_now = now or datetime.now(timezone.utc)
            if not self._repo.claim(row.id, now=claim_now, lease_seconds=self._lease):
                continue
            attempts = row.attempts + 1
            try:
                self._send_one(row, now or datetime.now(timezone.utc))
            except _Defer as defer:
                # Hand the attempt back: waiting for the reader's morning is
                # not a failed send.
                self._repo.mark_retry(
                    row.id, attempts=row.attempts, next_attempt_at=defer.until, error=row.last_error or ""
                )
                logger.info(
                    "lifecycle_message_deferred",
                    message_id=row.id,
                    message_key=row.message_key,
                    until=defer.until.isoformat(),
                    reason=str(defer),
                )
            except _Skip as skip:
                self._repo.mark_skipped(row.id, reason=str(skip))
                logger.info(
                    "lifecycle_message_skipped", message_id=row.id, message_key=row.message_key, reason=str(skip)
                )
            except Exception as exc:
                self._settle_retryable(row, attempts, str(exc), now or datetime.now(timezone.utc))
            else:
                self._repo.mark_sent(row.id, sent_at=now or datetime.now(timezone.utc))
                sent += 1
                logger.info(
                    "lifecycle_message_sent",
                    message_id=row.id,
                    user_id=row.user_id,
                    message_key=row.message_key,
                    attempts=attempts,
                )
        if due:
            logger.info("lifecycle_delivery_pass", due=len(due), sent=sent)
        return sent

    def _send_one(self, row: UserMessage, now: datetime) -> None:
        message = self._by_key.get(row.message_key)
        if message is None:
            raise _Skip("message no longer in the sequence")
        # The send-time re-check: the facts query only returns active users
        # with tips on, so an unsubscribe or a ban since planning wins.
        facts = self._repo.candidates(
            created_from=datetime.min.replace(tzinfo=timezone.utc),
            created_to=now,
            channel=_CHANNEL,
            limit=1,
            user_id=row.user_id,
        )
        if not facts:
            raise _Skip("tips off, account inactive or deleted")
        if now - facts[0].created_at >= message.expires_after:
            raise _Skip("expired")
        if not message.applies(facts[0]):
            raise _Skip("no longer applies")
        # Timing again, for a row sent later than planned (a retry, a
        # backlog): the reader's hours and the briefing-email gap. The
        # frequency cap needs no recheck: planning counts pending rows, so
        # no second lifecycle message can be queued while this one waits.
        until = self._blocked_until(facts[0], now)
        if until is not None:
            raise _Defer(until, "outside send window or briefing email sent recently")
        email = self._renderer.render(message, facts[0])
        self._sender.send(
            to=facts[0].email, subject=email.subject, html=email.html, text=email.text, headers=email.headers
        )

    def _settle_retryable(self, row: UserMessage, attempts: int, error: str, now: datetime) -> None:
        if attempts >= self._max_attempts:
            self._repo.mark_failed(row.id, attempts=attempts, error=error)
            logger.error(
                "lifecycle_message_failed",
                message_id=row.id,
                message_key=row.message_key,
                attempts=attempts,
                error=error,
            )
            return
        next_attempt_at = now + timedelta(seconds=self._backoff * (2 ** (attempts - 1)))
        self._repo.mark_retry(row.id, attempts=attempts, next_attempt_at=next_attempt_at, error=error)
        logger.warning(
            "lifecycle_message_retry_scheduled",
            message_id=row.id,
            message_key=row.message_key,
            attempts=attempts,
            next_attempt_at=next_attempt_at.isoformat(),
            error=error,
        )

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _zone(facts: LifecycleFacts):
        if facts.timezone_name:
            try:
                return ZoneInfo(facts.timezone_name)
            except (KeyError, ValueError):
                pass
        return timezone.utc

    def _in_send_window(self, facts: LifecycleFacts, now: datetime) -> bool:
        start, end = self._window
        return start <= now.astimezone(self._zone(facts)).hour < end

    def _blocked_until(self, facts: LifecycleFacts, now: datetime) -> Optional[datetime]:
        """The earliest time a send is allowed, or None when it is allowed now."""
        candidates: List[datetime] = []
        if not self._in_send_window(facts, now):
            local = now.astimezone(self._zone(facts))
            start = local.replace(hour=self._window[0], minute=0, second=0, microsecond=0)
            if local.hour >= self._window[0]:
                start += timedelta(days=1)
            candidates.append(start.astimezone(timezone.utc))
        if facts.last_briefing_email_at is not None:
            quiet_until = facts.last_briefing_email_at + self._briefing_quiet
            if quiet_until > now:
                candidates.append(quiet_until)
        return max(candidates) if candidates else None
