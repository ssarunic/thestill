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

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

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
        renderer: Optional["BriefingRenderer"] = None,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self._briefings = briefing_repository
        self._inbox = inbox_repository
        self._min_interval = timedelta(seconds=min_interval_seconds)
        self._renderer = renderer
        logger.info(
            "BriefingService initialized",
            min_interval_seconds=min_interval_seconds,
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
        """Builder that pulls ``briefing_min_interval_seconds`` from ``Config``."""
        return cls(
            briefing_repository,
            inbox_repository,
            min_interval_seconds=config.briefing_min_interval_seconds,
            renderer=renderer,
        )

    def generate_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
        now: Optional[datetime] = None,
    ) -> Optional[Briefing]:
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
        """
        clock_now = now or datetime.now(timezone.utc)
        latest = self._briefings.latest_for_user(user_id)

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
