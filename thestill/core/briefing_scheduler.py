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
"""

import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ..utils.briefing_cadence import next_run_for

if TYPE_CHECKING:
    from ..repositories.briefing_schedule_repository import BriefingScheduleRepository
    from ..services.briefing_service import BriefingService

logger = get_logger(__name__)


class BriefingScheduler:
    """Background tick that generates briefings for due user schedules."""

    def __init__(
        self,
        schedule_repository: "BriefingScheduleRepository",
        briefing_service: "BriefingService",
        *,
        tick_seconds: int = 60,
        max_per_tick: int = 50,
    ) -> None:
        self.schedule_repository = schedule_repository
        self.briefing_service = briefing_service
        self.tick_seconds = max(5, tick_seconds)
        self.max_per_tick = max_per_tick
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
        generated = 0
        for schedule in due:
            # ``next_run_at`` can't be None on a due row; assert for mypy.
            assert schedule.next_run_at is not None
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
                briefing = self.briefing_service.generate_for_user(schedule.user_id, now=clock_now)
            except Exception:
                logger.exception(
                    "briefing_scheduled_generation_failed",
                    user_id=schedule.user_id,
                    next_run_at=next_run.isoformat(),
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
            logger.info(
                "briefing_scheduled_generated",
                user_id=schedule.user_id,
                briefing_id=briefing.id,
                episode_count=briefing.episode_count,
                next_run_at=next_run.isoformat(),
            )
        if due:
            logger.info("briefing_scheduler_ticked", due=len(due), generated=generated)
        return generated
