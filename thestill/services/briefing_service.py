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
Per-user briefing service (spec #36).

Owns the state machine: cursor advancement, throttle window, the
"no eligible items → no briefing" decision, and persistence. Script
rendering is delegated to an optional ``BriefingRenderer`` so the
cursor logic stays free of file IO and tests can run row-only.
"""

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional, Union

from structlog import get_logger

from ..models.briefing import Briefing
from ..repositories.briefing_repository import BriefingRepository
from ..repositories.inbox_repository import InboxRepository

if TYPE_CHECKING:
    from ..utils.config import Config
    from .briefing_renderer import BriefingRenderer

logger = get_logger(__name__)

# First-run cursor: epoch covers the whole inbox.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class BriefingNotFoundError(Exception):
    """Raised when ``mark_listened`` targets a briefing that does not exist."""


@dataclass(frozen=True)
class Deferred:
    """A briefing generation attempt parked by the spec #55 readiness gate."""

    pending_count: int
    deadline: datetime


@dataclass(frozen=True)
class _LazyDeferral:
    """First lazy-open cutoff/deadline for one unchanged cursor window."""

    cursor_from: datetime
    cutoff: datetime
    deadline: datetime
    grace_exhausted: bool = False


class BriefingService:
    """
    Per-user briefing state machine.

    Selection: inbox rows in ``[cursor_from, cursor_to)`` whose ``state`` is
    ``unread`` or ``saved``. Read and dismissed rows are excluded — the
    briefing is a readout of *what the user hasn't acted on*.
    """

    def __init__(
        self,
        briefing_repository: BriefingRepository,
        inbox_repository: InboxRepository,
        *,
        min_interval_seconds: int,
        readiness_grace_minutes: int = 60,
        renderer: Optional["BriefingRenderer"] = None,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        if readiness_grace_minutes < 0:
            raise ValueError("readiness_grace_minutes must be non-negative")
        self._briefings = briefing_repository
        self._inbox = inbox_repository
        self._min_interval = timedelta(seconds=min_interval_seconds)
        self._readiness_grace = timedelta(minutes=readiness_grace_minutes)
        self._renderer = renderer
        self._lazy_deferrals: dict[str, _LazyDeferral] = {}
        self._lazy_deferrals_lock = threading.Lock()
        logger.info(
            "BriefingService initialized",
            min_interval_seconds=min_interval_seconds,
            readiness_grace_minutes=readiness_grace_minutes,
            rendering_enabled=renderer is not None,
        )

    @classmethod
    def from_config(
        cls,
        config: "Config",
        briefing_repository: BriefingRepository,
        inbox_repository: InboxRepository,
        *,
        renderer: Optional["BriefingRenderer"] = None,
    ) -> "BriefingService":
        """Build from the briefing throttle and readiness settings in ``Config``."""
        return cls(
            briefing_repository,
            inbox_repository,
            min_interval_seconds=config.briefing_min_interval_seconds,
            readiness_grace_minutes=config.briefing_readiness_grace_minutes,
            renderer=renderer,
        )

    def generate_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
        now: Optional[datetime] = None,
        cutoff: Optional[datetime] = None,
        readiness_deadline: Optional[datetime] = None,
    ) -> Union[Briefing, Deferred, None]:
        """
        Generate a briefing covering ``[last_cursor, now)`` for ``user_id``.

        Returns:
            - The newly-created briefing if eligible inbox items exist and
              the throttle has elapsed (or ``force=True``).
            - The existing latest briefing if generation is throttled (a
              briefing was created within ``min_interval_seconds`` and
              ``force=False``).
            - ``None`` if no eligible inbox items fall in the window —
              callers should treat this as a no-op.
            - ``Deferred`` when followed, pre-cutoff episodes in this cursor
              window still have active pipeline work and grace remains.
        """
        clock_now = now or datetime.now(timezone.utc)
        latest = self._briefings.latest_for_user(user_id)

        if force:
            logger.info("briefing_forced", user_id=user_id)

        if latest is not None and not force:
            elapsed = clock_now - latest.created_at
            if elapsed < self._min_interval:
                logger.debug(
                    "briefing_throttled",
                    user_id=user_id,
                    elapsed_seconds=elapsed.total_seconds(),
                )
                return latest

        cursor_from = latest.cursor_to if latest is not None else _EPOCH
        cursor_to = clock_now
        if force:
            # Preserve the anchor when a forced cut has nothing ready. Marking
            # it exhausted prevents the next 5-second UI poll from silently
            # minting a brand-new grace window for the same wait-set.
            self._mark_lazy_deferral_exhausted(user_id, cursor_from)

        lazy: Optional[_LazyDeferral] = None
        if not force and self._readiness_grace > timedelta(0):
            is_scheduled = cutoff is not None or readiness_deadline is not None
            if is_scheduled:
                effective_cutoff = cutoff or clock_now
                deadline = readiness_deadline or (clock_now + self._readiness_grace)
            else:
                lazy = self._get_or_create_lazy_deferral(user_id, cursor_from, clock_now)
                effective_cutoff = lazy.cutoff
                deadline = lazy.deadline

            try:
                pending_count = self._briefings.count_pending_for_user(
                    user_id,
                    since=cursor_from,
                    cutoff=effective_cutoff,
                )
            except Exception:
                # FM-1: readiness is an availability enhancement, never a
                # reason to suppress a briefing when its query is unhealthy.
                logger.exception(
                    "briefing_readiness_check_failed",
                    user_id=user_id,
                    cutoff=effective_cutoff.isoformat(),
                )
                if lazy is not None:
                    self._mark_lazy_deferral_exhausted(user_id, cursor_from)
            else:
                if pending_count:
                    if lazy is not None and lazy.grace_exhausted:
                        # Deadline expiry / Generate-now is a durable bypass
                        # for this original wait-set. Keep checking only so we
                        # can clear it once the work drains; never defer again.
                        pass
                    elif clock_now < deadline:
                        logger.info(
                            "briefing_deferred",
                            user_id=user_id,
                            pending_count=pending_count,
                            cutoff=effective_cutoff.isoformat(),
                            deadline=deadline.isoformat(),
                        )
                        return Deferred(pending_count=pending_count, deadline=deadline)
                    else:
                        logger.warning(
                            "briefing_grace_expired",
                            user_id=user_id,
                            abandoned_count=pending_count,
                        )
                        if lazy is not None:
                            self._mark_lazy_deferral_exhausted(user_id, cursor_from)
                elif lazy is not None:
                    self._clear_lazy_deferral(user_id)

        episode_ids = self._inbox.list_episode_ids_in_window(user_id, since=cursor_from, until=cursor_to)
        if not episode_ids:
            logger.debug(
                "briefing_empty_inbox",
                user_id=user_id,
                cursor_from=cursor_from.isoformat(),
                cursor_to=cursor_to.isoformat(),
            )
            return None

        briefing = Briefing(
            user_id=user_id,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
            episode_count=len(episode_ids),
            created_at=clock_now,
        )
        # Render before insert so a render failure leaves no orphan row
        # and the cursor doesn't advance — the next call retries the
        # same window cleanly.
        if self._renderer is not None:
            script_path = self._renderer.render(briefing, episode_ids)
            briefing.script_path = str(script_path)
        self._briefings.insert(briefing)
        self._clear_lazy_deferral(user_id)
        logger.info(
            "briefing_generated",
            user_id=user_id,
            briefing_id=briefing.id,
            episode_count=briefing.episode_count,
            cursor_from=cursor_from.isoformat(),
            cursor_to=cursor_to.isoformat(),
            script_path=briefing.script_path,
        )
        return briefing

    def _get_or_create_lazy_deferral(
        self,
        user_id: str,
        cursor_from: datetime,
        clock_now: datetime,
    ) -> _LazyDeferral:
        """Anchor lazy grace/cutoff at the first request for this cursor."""
        with self._lazy_deferrals_lock:
            existing = self._lazy_deferrals.get(user_id)
            if existing is not None and existing.cursor_from == cursor_from:
                if existing.grace_exhausted or clock_now <= existing.deadline + self._readiness_grace:
                    return existing
                # An expired anchor that was never observed at its deadline is
                # a stale browser session, not an exhausted grace decision.
                # Re-anchor so a next-day open includes the fresh backlog.
            created = _LazyDeferral(
                cursor_from=cursor_from,
                cutoff=clock_now,
                deadline=clock_now + self._readiness_grace,
            )
            self._lazy_deferrals[user_id] = created
            return created

    def _mark_lazy_deferral_exhausted(self, user_id: str, cursor_from: datetime) -> None:
        with self._lazy_deferrals_lock:
            existing = self._lazy_deferrals.get(user_id)
            if existing is None or existing.cursor_from != cursor_from:
                return
            self._lazy_deferrals[user_id] = _LazyDeferral(
                cursor_from=existing.cursor_from,
                cutoff=existing.cutoff,
                deadline=existing.deadline,
                grace_exhausted=True,
            )

    def _clear_lazy_deferral(self, user_id: str) -> None:
        with self._lazy_deferrals_lock:
            self._lazy_deferrals.pop(user_id, None)

    def latest_for_user(self, user_id: str) -> Optional[Briefing]:
        """Return the user's most recent briefing, or ``None``."""
        return self._briefings.latest_for_user(user_id)

    def mark_listened(
        self,
        briefing_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Briefing:
        """
        Set ``listened_at`` on the briefing row.

        Idempotent: re-marking overwrites ``listened_at``. Callers that
        need the first-listened timestamp should capture it elsewhere.

        Raises:
            BriefingNotFoundError: if ``briefing_id`` is unknown.
        """
        clock_now = now or datetime.now(timezone.utc)
        updated = self._briefings.update_listened_at(briefing_id, clock_now)
        if updated is None:
            raise BriefingNotFoundError(f"No briefing with id={briefing_id!r}")
        logger.info("briefing_listened", briefing_id=briefing_id)
        return updated
