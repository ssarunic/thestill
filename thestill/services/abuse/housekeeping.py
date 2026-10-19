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

"""Spec #104 §2.2 — keeping the usage ledger honest.

- An abandoned reservation (the process died between reserve and settle)
  keeps counting, so the system fails closed; after
  ``QUOTA_RESERVATION_TTL_SECONDS`` it is released. A burst of these points
  at a crash loop.
- Rows older than 90 days are deleted, except ``feed_introduced`` rows,
  which say who brought a feed in (§1.5).
"""

from datetime import datetime, timedelta
from typing import Callable, List

from structlog import get_logger

from ...core.periodic_runner import PeriodicJob
from ...repositories.usage_event_repository import UsageEventRepository
from ...utils.datetime_utils import now_utc

logger = get_logger(__name__)

USAGE_RETENTION = timedelta(days=90)


def usage_housekeeping_jobs(
    usage: UsageEventRepository,
    *,
    reservation_ttl_seconds: int,
    clock: Callable[[], datetime] = now_utc,
) -> List[PeriodicJob]:
    def release_stale_reservations() -> None:
        now = clock()
        released = usage.release_stale(now - timedelta(seconds=reservation_ttl_seconds), now=now)
        if released:
            logger.warning("quota_reservation_expired", count=released)

    def prune_usage_events() -> None:
        pruned = usage.prune(clock() - USAGE_RETENTION)
        if pruned:
            logger.info("usage_events_pruned", count=pruned)

    return [
        PeriodicJob("release_stale_reservations", 60, release_stale_reservations),
        PeriodicJob("prune_usage_events", 6 * 3600, prune_usage_events),
    ]
