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
Per-user digest service.

Generates a digest covering the inbox window
``[last_digest.period_end, now)`` for a single user. Replaces the legacy
global ``since_days`` selector path on the user-facing "Today's briefing"
flow: selection comes from the inbox (spec #29 fan-out), the cursor chain
guarantees each delivered episode is briefed exactly once, and a throttle
window collapses accidental double-triggers (cron racing the UI).

The digest row stays in the existing ``digests`` table — ``period_end``
plays the role the briefing system's ``cursor_to`` did.
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from structlog import get_logger

from ..models.digest import Digest
from ..models.podcast import Episode, Podcast
from ..repositories.digest_repository import DigestRepository
from ..repositories.inbox_repository import InboxRepository
from ..repositories.podcast_repository import PodcastRepository
from .digest_generator import DigestGenerator

if TYPE_CHECKING:
    from ..utils.config import Config
    from ..utils.path_manager import PathManager

logger = get_logger(__name__)

# First-run cursor: epoch covers the whole inbox.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class DigestService:
    """
    Per-user digest state machine.

    Selection: inbox rows in ``[cursor_from, now)`` whose ``state`` is
    ``unread`` or ``saved``. Read and dismissed rows are excluded — the
    digest is a readout of *what the user hasn't acted on*.

    The cursor chain ``digest_n.period_end == digest_n+1.period_start``
    guarantees each delivered episode lands in at most one digest. The
    throttle window collapses accidental rapid-fire triggers (e.g. a
    cron job that ran three times in 1.5 seconds because of a misfire).
    """

    def __init__(
        self,
        digest_repository: DigestRepository,
        inbox_repository: InboxRepository,
        podcast_repository: PodcastRepository,
        digest_generator: DigestGenerator,
        path_manager: "PathManager",
        *,
        min_interval_seconds: int,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        self._digests = digest_repository
        self._inbox = inbox_repository
        self._episodes = podcast_repository
        self._generator = digest_generator
        self._paths = path_manager
        self._min_interval = timedelta(seconds=min_interval_seconds)
        logger.info(
            "DigestService initialized",
            min_interval_seconds=min_interval_seconds,
        )

    @classmethod
    def from_config(
        cls,
        config: "Config",
        digest_repository: DigestRepository,
        inbox_repository: InboxRepository,
        podcast_repository: PodcastRepository,
        digest_generator: DigestGenerator,
        path_manager: "PathManager",
    ) -> "DigestService":
        return cls(
            digest_repository,
            inbox_repository,
            podcast_repository,
            digest_generator,
            path_manager,
            min_interval_seconds=config.digest_min_interval_seconds,
        )

    def generate_for_user(
        self,
        user_id: str,
        *,
        force: bool = False,
        now: Optional[datetime] = None,
    ) -> Optional[Digest]:
        """
        Generate a digest covering ``[last_period_end, now)`` for ``user_id``.

        Returns:
            - The newly-created digest if eligible inbox items exist and
              the throttle has elapsed (or ``force=True``).
            - The existing latest digest if generation is throttled.
            - ``None`` if no eligible inbox items fall in the window —
              callers should treat this as a no-op (UI hides the card).
        """
        clock_now = now or datetime.now(timezone.utc)
        latest = self.latest_for_user(user_id)

        if latest is not None and not force:
            elapsed = clock_now - latest.created_at
            if elapsed < self._min_interval:
                logger.debug(
                    "digest_throttled",
                    user_id=user_id,
                    elapsed_seconds=elapsed.total_seconds(),
                    latest_digest_id=latest.id,
                )
                return latest

        period_start = latest.period_end if latest is not None else _EPOCH
        period_end = clock_now

        episode_ids = self._inbox.list_episode_ids_in_window(
            user_id,
            since=period_start,
            until=period_end,
        )
        if not episode_ids:
            logger.debug(
                "digest_empty_inbox",
                user_id=user_id,
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
            )
            return None

        episodes = self._hydrate_episodes(episode_ids)
        if not episodes:
            # Inbox window had IDs, but all episodes have since been deleted.
            # Advancing the cursor here would lose nothing, but the contract
            # ("returns the produced digest or None") is cleaner if we treat
            # the empty hydrated set as "no eligible content" and don't
            # write a row.
            logger.warning(
                "digest_all_episodes_missing",
                user_id=user_id,
                episode_ids=episode_ids,
            )
            return None

        digest = Digest(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            episode_ids=[ep.id for _, ep in episodes],
            episodes_total=len(episodes),
        )

        # Render before persist so a render failure leaves no orphan row
        # and the cursor doesn't advance — next call retries the same
        # window cleanly.
        start = time.time()
        content = self._generator.generate(episodes=episodes)
        output_filename = f"digest_{clock_now.strftime('%Y%m%d_%H%M%S')}_{digest.id[:8]}.md"
        output_path = self._paths.digest_file(output_filename)
        self._generator.write(content, output_path)
        processing_time = time.time() - start

        digest.mark_completed(
            file_path=output_filename,
            episodes_completed=len(episodes),
            episodes_failed=0,
            processing_time_seconds=processing_time,
        )
        self._digests.save(digest)

        logger.info(
            "digest_generated",
            user_id=user_id,
            digest_id=digest.id,
            episode_count=len(episodes),
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            file_path=output_filename,
        )
        return digest

    def latest_for_user(self, user_id: str) -> Optional[Digest]:
        """Return the user's most recent digest, or ``None``."""
        rows = self._digests.get_all(limit=1, offset=0, user_id=user_id)
        return rows[0] if rows else None

    def _hydrate_episodes(self, episode_ids: List[str]) -> List[Tuple[Podcast, Episode]]:
        episodes: List[Tuple[Podcast, Episode]] = []
        for episode_id in episode_ids:
            row = self._episodes.get_episode(episode_id)
            if row is None:
                logger.warning(
                    "digest_render_skipped_missing_episode",
                    episode_id=episode_id,
                )
                continue
            episodes.append(row)
        return episodes
