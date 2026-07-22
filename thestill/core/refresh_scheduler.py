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

"""Spec #48 — background refresh scheduler.

A lightweight daemon-thread loop that, on each tick, enqueues a ``REFRESH_FEED``
task for every **due** feed (``next_refresh_at <= now``) instead of refreshing
all feeds in one burst. The tick interval is the scheduling *granularity*; the
per-feed cadence (set adaptively by the handler) is independent.

The loop is cheap: the due-query is a single indexed range scan, and the
per-feed coalescing guard (``add_feed_task`` / ``has_pending_feed_task``) keeps
a feed from being enqueued twice. Terminally-failed feeds park themselves
(``next_refresh_at = NULL``) and are excluded by the due-query until an operator
re-arms them, so a dead feed never burns a slot every tick.
"""

import threading
from typing import TYPE_CHECKING

from structlog import get_logger

from .queue_manager import TaskStage

if TYPE_CHECKING:
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from .queue_manager import QueueManager

logger = get_logger(__name__)


class RefreshScheduler:
    """Background tick that enqueues due feeds as REFRESH_FEED tasks."""

    def __init__(
        self,
        repository: "SqlitePodcastRepository",
        queue_manager: "QueueManager",
        *,
        tick_seconds: int = 60,
        default_interval_seconds: int = 3600,
        max_enqueue_per_tick: int = 500,
        quarantine_probe_interval_seconds: int = 7 * 86400,
    ) -> None:
        self.repository = repository
        self.queue_manager = queue_manager
        self.tick_seconds = max(5, tick_seconds)
        self.default_interval_seconds = default_interval_seconds
        self.max_enqueue_per_tick = max_enqueue_per_tick
        self.quarantine_probe_interval_seconds = quarantine_probe_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("refresh_scheduler_already_running")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="refresh-scheduler", daemon=True)
        self._thread.start()
        logger.info(
            "refresh_scheduler_started",
            tick_seconds=self.tick_seconds,
            default_interval_seconds=self.default_interval_seconds,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("refresh_scheduler_stopped")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_loop(self) -> None:
        # Run a tick immediately on start, then every ``tick_seconds`` until
        # stopped. ``Event.wait`` returns True the moment stop() is signalled,
        # so shutdown is prompt rather than blocking out the full interval.
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("refresh_scheduler_tick_failed")
            self._stop.wait(self.tick_seconds)

    def tick(self) -> int:
        """One scheduling pass. Returns the number of feeds enqueued."""
        # Seed any never-scheduled active feed (e.g. just added) so it becomes
        # due; parked/quarantined feeds are left alone.
        self.repository.seed_unscheduled_feeds(self.default_interval_seconds)

        due = self.repository.get_due_podcasts(limit=self.max_enqueue_per_tick)
        # Spec #60 — quarantined feed_gone/invalid_content feeds get one
        # low-frequency re-probe (weekly by default). The probe rides the
        # normal REFRESH_FEED path: success un-quarantines via
        # record_refresh_success; failure re-quarantines via the policy.
        probes = self.repository.get_quarantine_probe_due(
            self.quarantine_probe_interval_seconds,
            limit=self.max_enqueue_per_tick,
        )
        enqueued = 0
        for podcast_id in [*due, *probes]:
            # Coalescing: skip if a non-terminal REFRESH_FEED already exists.
            if self.queue_manager.has_pending_feed_task(podcast_id, TaskStage.REFRESH_FEED):
                continue
            task = self.queue_manager.add_feed_task(podcast_id, TaskStage.REFRESH_FEED)
            if task is not None:
                enqueued += 1
        if enqueued:
            logger.info(
                "refresh_scheduler_enqueued",
                due=len(due),
                quarantine_probes=len(probes),
                enqueued=enqueued,
            )
        return enqueued
