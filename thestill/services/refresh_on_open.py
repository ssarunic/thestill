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

"""Spec #74 — refresh-on-open.

The universal follower gate (spec #63) stops the scheduler polling feeds
nobody follows. That is right for the paid pipeline and wrong for browsing:
a Top-chart show opened from the chart froze at whatever its last refresh
found. Opening a podcast is the one demand signal the gate cannot see, so
this service turns it into at most one ``REFRESH_FEED`` task:

- **Discovery only for unfollowed feeds.** The enqueue-point gate in
  ``enqueue_discovered_episodes`` keeps "processed = followed" intact; the
  task itself is the same one the scheduler enqueues.
- **The first discovery is the same task.** The resolve endpoint (lazy
  import) calls this service too, so initial discovery is a durable,
  observable, retryable queue row rather than a fire-and-forget thread —
  and ``pending`` reports it to the detail page like any other refresh.
- **Coalesced and promoted.** The queue's per-feed uniqueness guard makes a
  reload a no-op; a queued (not yet running) task left behind by the
  scheduler at priority 0 is promoted to reader priority so it does not
  wait behind a scheduler burst.
- **Honours server-directed cooldowns.** A ``Retry-After`` recorded by the
  handler (``refresh_retry_after_at``) suppresses opens until it expires.
- **Throttled per feed** by ``REFRESH_MIN_INTERVAL_SECONDS`` measured from
  ``last_refresh_at`` — written on success *and* failure by the handler —
  so repeated opens never poll a feed faster than the scheduler's floor.
- **Never for quarantined feeds** (``refresh_disabled_reason`` set); those
  have their own probe/operator path (spec #60).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ..core.queue_manager import TaskStage
from ..utils.datetime_utils import now_utc

if TYPE_CHECKING:
    from ..repositories.podcast_repository import PodcastRepository

logger = get_logger(__name__)

# Spec #48's "freshness" priority: a reader is waiting on this one, so it
# jumps any scheduler backfill sitting in the REFRESH_FEED lane.
OPEN_REFRESH_PRIORITY = 10


class RefreshOnOpenOutcome(str, Enum):
    """Why an open did (or did not) enqueue a refresh."""

    ENQUEUED = "enqueued"
    COALESCED = "coalesced"  # a REFRESH_FEED for this feed is already queued/running
    BACKING_OFF = "backing_off"  # server-directed Retry-After not yet expired
    THROTTLED = "throttled"  # refreshed within the min interval
    QUARANTINED = "quarantined"
    NOT_FOUND = "not_found"
    DISABLED = "disabled"

    @property
    def pending(self) -> bool:
        """True while a refresh for the feed is queued or running."""
        return self in (RefreshOnOpenOutcome.ENQUEUED, RefreshOnOpenOutcome.COALESCED)


class RefreshOnOpenService:
    """Turn a podcast open (or lazy import) into at most one ``REFRESH_FEED``."""

    def __init__(
        self,
        repository: "PodcastRepository",
        queue_manager,
        *,
        min_interval_seconds: int,
        enabled: bool = True,
    ) -> None:
        self.repository = repository
        self.queue_manager = queue_manager
        self.min_interval = timedelta(seconds=max(0, min_interval_seconds))
        self.enabled = enabled

    def maybe_trigger(self, podcast_id: str, now: Optional[datetime] = None) -> RefreshOnOpenOutcome:
        """Enqueue a refresh for ``podcast_id`` unless a guard says otherwise.

        Cheap on the hot path: one bookkeeping read, one pending-task probe,
        and (rarely) one insert behind the queue's uniqueness guard.
        """
        if not self.enabled:
            return RefreshOnOpenOutcome.DISABLED

        state = self.repository.get_refresh_on_open_state(podcast_id)
        if state is None:
            return RefreshOnOpenOutcome.NOT_FOUND
        if state.refresh_disabled_reason:
            return RefreshOnOpenOutcome.QUARANTINED

        # Probe before the cooldown/throttle so a refresh that is already
        # queued (scheduler tick, lazy import, concurrent open) is reported
        # as pending rather than hidden behind an interval — and promoted
        # to reader priority if it is still waiting in the queue.
        if self.queue_manager.has_pending_feed_task(podcast_id, TaskStage.REFRESH_FEED):
            return self._coalesce(podcast_id)

        now_dt = now or now_utc()
        if state.refresh_retry_after_at is not None and state.refresh_retry_after_at > now_dt:
            return RefreshOnOpenOutcome.BACKING_OFF
        if state.last_refresh_at is not None and now_dt - state.last_refresh_at < self.min_interval:
            return RefreshOnOpenOutcome.THROTTLED

        task = self.queue_manager.add_feed_task(
            podcast_id,
            TaskStage.REFRESH_FEED,
            priority=OPEN_REFRESH_PRIORITY,
            metadata={"initiated_by": "open"},
        )
        if task is None:
            # Lost the race to a concurrent open/scheduler tick — same effect.
            return self._coalesce(podcast_id)

        logger.info(
            "refresh_on_open_enqueued",
            podcast_id=podcast_id,
            task_id=task.id,
            last_refresh_at=state.last_refresh_at.isoformat() if state.last_refresh_at else None,
        )
        return RefreshOnOpenOutcome.ENQUEUED

    def _coalesce(self, podcast_id: str) -> RefreshOnOpenOutcome:
        promoted = self.queue_manager.promote_pending_feed_task(
            podcast_id, TaskStage.REFRESH_FEED, OPEN_REFRESH_PRIORITY
        )
        if promoted:
            logger.info("refresh_on_open_promoted", podcast_id=podcast_id, priority=OPEN_REFRESH_PRIORITY)
        return RefreshOnOpenOutcome.COALESCED
