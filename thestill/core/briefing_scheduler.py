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

"""Spec #50 — scheduled briefings.

A daemon-thread loop (pattern-matches ``RefreshScheduler``, spec #48) that,
on each tick, generates a briefing for every user whose schedule is **due**
(``next_run_at <= now``). The tick interval is the scheduling granularity;
the per-user cadence (daily / weekly at ``hour_local`` in their timezone)
lives on the ``user_briefing_schedules`` row.

Claim-before-generate: each due slot is taken by a conditional UPDATE that
advances ``next_run_at`` to the next *future* occurrence before generation
runs. That makes the tick safe under multiple server instances (only one
wins the UPDATE), guarantees a crashed generation doesn't re-fire every
tick, and makes downtime catch-up fire exactly once — the recomputed slot
is "next occurrence after now", never a replay of missed slots.

Generation itself is ``BriefingService.generate_for_user`` — cursor math,
the min-interval throttle, and the empty-window no-op all apply unchanged.

Phase 4 (#33 interlock): when a ``NarrationRunner`` is provided, each
scheduled run chains narration after script generation, so the listenable
artefact — not just the script — exists by ``hour_local``. Narration is
best-effort and idempotent per ``(briefing, slug)``: a failure never fails
the run, and an already-narrated briefing (e.g. throttle-returned after a
lazy open + manual narrate) isn't re-spent.

Spec #51: when a ``BriefingDeliveryService`` is provided, a slot firing
with ``email_enabled`` ensures a delivery row exists for the briefing —
whether generation returned a fresh briefing or the throttled existing one
("send if this briefing hasn't been emailed yet", never "send if a new
briefing was generated"). The tick then runs a second phase, the delivery
pass, which sends every claimable delivery. Email failure retries on the
delivery row's own cadence and never touches the schedule cursor.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ..services.briefing_service import Deferred
from ..utils.briefing_cadence import latest_run_for, next_run_for
from ..utils.duration import slug_for_duration_seconds

if TYPE_CHECKING:
    from ..models.briefing import Briefing
    from ..repositories.briefing_schedule_repository import BriefingScheduleRepository
    from ..services.briefing_delivery_service import BriefingDeliveryService
    from ..services.briefing_service import BriefingService
    from ..services.narration import NarrationRunner

logger = get_logger(__name__)


@dataclass(frozen=True)
class _PendingBriefing:
    """One claimed schedule slot parked by the spec #55 readiness gate."""

    cutoff: datetime
    deadline: datetime


class BriefingScheduler:
    """Background tick that generates briefings for due user schedules."""

    def __init__(
        self,
        schedule_repository: "BriefingScheduleRepository",
        briefing_service: "BriefingService",
        *,
        tick_seconds: int = 60,
        max_per_tick: int = 50,
        narration_runner: "Optional[NarrationRunner]" = None,
        narration_target_seconds: int = 300,
        delivery_service: "Optional[BriefingDeliveryService]" = None,
    ) -> None:
        self.schedule_repository = schedule_repository
        self.briefing_service = briefing_service
        self.tick_seconds = max(5, tick_seconds)
        self.max_per_tick = max_per_tick
        self.narration_runner = narration_runner
        self.narration_target_seconds = narration_target_seconds
        self.delivery_service = delivery_service
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Briefings whose ensure_pending blew up (e.g. a DB hiccup at slot
        # time). The slot has already advanced, so without a retry the
        # scheduled email is lost until the next cadence — re-attempt the
        # (idempotent) queueing on every tick until it lands.
        self._unqueued_deliveries: set[str] = set()
        # Spec #55: #50 advances the schedule row before generation, so a
        # deferred slot must remain reachable in memory until it resolves.
        self._pending_briefings: dict[str, _PendingBriefing] = {}

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("briefing_scheduler_already_running")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="briefing-scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "briefing_scheduler_started",
            tick_seconds=self.tick_seconds,
            max_per_tick=self.max_per_tick,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("briefing_scheduler_stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        # Tick immediately on start (a slot missed during downtime becomes
        # due the moment the server is back), then every ``tick_seconds``.
        # ``Event.wait`` returns as soon as stop() fires, so shutdown is
        # prompt rather than blocking out the full interval.
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("briefing_scheduler_tick_failed")
            self._stop.wait(self.tick_seconds)

    def tick(self, now: Optional[datetime] = None) -> int:
        """One scheduling pass. Returns the number of briefings generated."""
        clock_now = now or datetime.now(timezone.utc)
        due = self.schedule_repository.due(clock_now, limit=self.max_per_tick)
        generated = self._retry_pending_briefings(clock_now)
        for schedule in due:
            # A grace longer than the schedule cadence is unusual but valid.
            # Do not claim a second slot for the same user while the first is
            # still parked; leaving it due makes the next tick reconsider it.
            if schedule.user_id in self._pending_briefings:
                continue
            # ``next_run_at`` can't be None on a due row; assert for mypy.
            assert schedule.next_run_at is not None
            # A due row can be days stale after downtime. Readiness must cover
            # releases through the latest nominal slot that elapsed before the
            # wake, not stop at the oldest missed ``next_run_at``.
            cutoff = latest_run_for(schedule, at=clock_now)
            next_run = next_run_for(schedule, after=clock_now)
            claimed = self.schedule_repository.claim(
                schedule.user_id,
                expected_next_run_at=schedule.next_run_at,
                new_next_run_at=next_run,
            )
            if not claimed:
                # Another instance took this slot, or the user edited the
                # schedule mid-tick. Either way it's not ours anymore.
                continue
            # Per-user isolation (FM-1): one user's failed generation must
            # not stall the fleet's mornings. The slot is already advanced,
            # so a persistent failure surfaces in logs once per cadence
            # instead of burning every tick.
            try:
                briefing = self.briefing_service.generate_for_user(
                    schedule.user_id,
                    now=clock_now,
                    cutoff=cutoff,
                )
            except Exception:
                logger.exception(
                    "briefing_scheduled_generation_failed",
                    user_id=schedule.user_id,
                    next_run_at=next_run.isoformat(),
                )
                continue
            if isinstance(briefing, Deferred):
                self._pending_briefings[schedule.user_id] = _PendingBriefing(
                    cutoff=cutoff,
                    deadline=briefing.deadline,
                )
                continue
            if briefing is None:
                # Empty inbox window: honest no-op, no filler briefing.
                logger.info(
                    "briefing_scheduled_skipped_empty",
                    user_id=schedule.user_id,
                    next_run_at=next_run.isoformat(),
                )
                continue
            generated += 1
            self._finish_generation(
                schedule.user_id,
                briefing,
                now=clock_now,
                next_run_at=next_run,
                email_enabled=schedule.email_enabled,
                from_deferral=False,
            )
        if due:
            logger.info("briefing_scheduler_ticked", due=len(due), generated=generated)
        # Second phase (spec #51): the delivery pass. Runs every tick so
        # backoff retries fire on their own cadence even when no slot is
        # due. Absent when EMAIL_PROVIDER=none — zero overhead.
        if self.delivery_service is not None:
            self._retry_unqueued_deliveries(clock_now)
            try:
                self.delivery_service.deliver_due(clock_now)
            except Exception:
                logger.exception("briefing_delivery_pass_failed")
        return generated

    def _retry_pending_briefings(self, now: datetime) -> int:
        """Re-check every spec #55 deferral once per scheduler tick."""
        generated = 0
        for user_id, pending in list(self._pending_briefings.items()):
            try:
                briefing = self.briefing_service.generate_for_user(
                    user_id,
                    now=now,
                    cutoff=pending.cutoff,
                    readiness_deadline=pending.deadline,
                )
            except Exception:
                logger.exception(
                    "briefing_deferred_generation_failed",
                    user_id=user_id,
                    cutoff=pending.cutoff.isoformat(),
                    deadline=pending.deadline.isoformat(),
                )
                if now >= pending.deadline:
                    # The slot already advanced when it first deferred. Do not
                    # let a failing retry leave this user in the pending map
                    # forever and suppress every future cadence.
                    self._pending_briefings.pop(user_id, None)
                    logger.error(
                        "briefing_deferred_abandoned_after_failure",
                        user_id=user_id,
                        cutoff=pending.cutoff.isoformat(),
                        deadline=pending.deadline.isoformat(),
                    )
                continue
            if isinstance(briefing, Deferred):
                if now >= pending.deadline:
                    # Defensive contract guard: the service should cut at the
                    # deadline, but a bad/mocked implementation must not park
                    # the user's future schedule indefinitely.
                    self._pending_briefings.pop(user_id, None)
                    logger.error(
                        "briefing_deferred_returned_after_deadline",
                        user_id=user_id,
                        deadline=pending.deadline.isoformat(),
                    )
                continue

            self._pending_briefings.pop(user_id, None)
            if briefing is None:
                logger.info("briefing_scheduled_skipped_empty_after_deferral", user_id=user_id)
                continue

            schedule = self.schedule_repository.get(user_id)
            generated += 1
            self._finish_generation(
                user_id,
                briefing,
                now=now,
                next_run_at=schedule.next_run_at if schedule else None,
                email_enabled=bool(schedule and schedule.email_enabled),
                from_deferral=True,
            )
        return generated

    def _finish_generation(
        self,
        user_id: str,
        briefing: "Briefing",
        *,
        now: datetime,
        next_run_at: Optional[datetime],
        email_enabled: bool,
        from_deferral: bool,
    ) -> None:
        """Run logging, delivery queueing, and narration for a resolved slot."""
        logger.info(
            "briefing_scheduled_generated",
            user_id=user_id,
            briefing_id=briefing.id,
            episode_count=briefing.episode_count,
            next_run_at=next_run_at.isoformat() if next_run_at else None,
            from_deferral=from_deferral,
        )
        # Spec #51: queue email before the slow narration chain so a crash
        # there cannot drop delivery. Current schedule state is respected
        # when a deferred slot resolves (a user can opt out during grace).
        if email_enabled and self.delivery_service is not None:
            self._queue_delivery(briefing.id, user_id, now)
        self._chain_narration(user_id, briefing)

    def _queue_delivery(self, briefing_id: str, user_id: str, now) -> None:
        """Ensure a delivery row exists; buffer the id for retry on failure.

        FM-1: a queueing blow-up (e.g. a DB hiccup at slot time) must not
        fail the user's generation or the fleet — but the slot has already
        advanced, so simply logging would lose the scheduled email until
        the next cadence. The id is retried each tick (ensure_pending is
        idempotent) until the row lands.
        """
        try:
            self.delivery_service.ensure_pending(briefing_id, now=now)
            self._unqueued_deliveries.discard(briefing_id)
        except Exception:
            self._unqueued_deliveries.add(briefing_id)
            logger.exception(
                "briefing_delivery_queue_failed",
                user_id=user_id,
                briefing_id=briefing_id,
                will_retry_next_tick=True,
            )

    def _retry_unqueued_deliveries(self, now) -> None:
        for briefing_id in list(self._unqueued_deliveries):
            try:
                self.delivery_service.ensure_pending(briefing_id, now=now)
            except Exception:
                logger.exception("briefing_delivery_queue_retry_failed", briefing_id=briefing_id)
                continue
            self._unqueued_deliveries.discard(briefing_id)
            logger.info("briefing_delivery_queue_recovered", briefing_id=briefing_id)

    def _chain_narration(self, user_id: str, briefing: "Briefing") -> None:
        """Phase 4 (#33 interlock): narrate the scheduled briefing.

        The throttle-returned case (a 7:30 lazy open followed by the 8:00
        scheduled slot) hands us a briefing that may already have a
        narration for this slug — the artefact existence check makes the
        chain idempotent instead of re-spending the LLM call. A briefing
        that was lazily *generated* but never narrated still gets its
        narration here, which is the "ready by morning" promise.

        Best-effort: a narration failure is logged (FM-1 isolation) and
        never fails the run — the script exists, only the readout is
        missing, and the next cadence slot narrates a fresh briefing.
        """
        if self.narration_runner is None:
            return
        slug = slug_for_duration_seconds(self.narration_target_seconds)
        if self.narration_runner.artifact_exists(briefing_id=briefing.id, slug=slug):
            return
        try:
            run = self.narration_runner.run(
                briefing_id=briefing.id,
                target_duration_seconds=self.narration_target_seconds,
                slug=slug,
            )
        except Exception:
            logger.exception(
                "briefing_scheduled_narration_failed",
                user_id=user_id,
                briefing_id=briefing.id,
                slug=slug,
            )
            return
        logger.info(
            "briefing_scheduled_narrated",
            user_id=user_id,
            briefing_id=briefing.id,
            narration_id=run.narration_id,
            mode=run.content.mode,
        )
