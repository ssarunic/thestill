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
Per-user inbox service.

Encapsulates the two delivery paths:

- ``fanout_on_publish``: an episode just transitioned to published; insert
  one row per follower of its podcast (``follow_new`` source).
- ``seed_on_follow``: a user just followed a podcast; deliver up to N
  recent published episodes (``follow_seed`` source).

Plus the read/state-mutation APIs used by the inbox view.
"""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable, List, Optional

from structlog import get_logger

from ..models.inbox import INBOX_STATES, InboxEntry, InboxItem
from ..repositories.inbox_repository import InboxRepository
from ..repositories.podcast_follower_repository import PodcastFollowerRepository

if TYPE_CHECKING:
    from ..core.queue_manager import QueueManager
    from ..repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from ..utils.config import Config

logger = get_logger(__name__)


class InboxServiceError(Exception):
    """Base exception for inbox service errors."""


class InvalidInboxStateError(InboxServiceError):
    """Raised when an unknown state value is passed to ``mark_state``."""


class InboxEntryNotFoundError(InboxServiceError):
    """Raised when ``mark_state`` targets a (user, episode) pair that has no row."""


class InboxService:
    """
    Service for delivering episodes to user inboxes and reading/mutating
    inbox state.
    """

    def __init__(
        self,
        inbox_repository: InboxRepository,
        follower_repository: PodcastFollowerRepository,
        *,
        seed_on_follow_count: int = 2,
        queue_manager: Optional["QueueManager"] = None,
        podcast_repository: Optional["SqlitePodcastRepository"] = None,
        transcription_provider: str = "",
    ) -> None:
        if seed_on_follow_count < 0:
            raise ValueError("seed_on_follow_count must be non-negative")
        self._repository = inbox_repository
        self._followers = follower_repository
        self._seed_count = seed_on_follow_count
        # Optional transcription plumbing. When both are supplied, episodes
        # delivered to an inbox (follow-seed, publish fan-out) are submitted for
        # the URL-optimized full pipeline so a brand-new follower's inbox fills
        # with readable summaries without a manual transcribe step. Best-effort:
        # absent deps disable the behavior; enqueue failures never break delivery.
        self._queue = queue_manager
        self._podcasts = podcast_repository
        self._transcription_provider = transcription_provider
        logger.info(
            "InboxService initialized",
            seed_on_follow=seed_on_follow_count,
            auto_transcribe=bool(queue_manager and podcast_repository),
        )

    @classmethod
    def from_config(
        cls,
        config: "Config",
        inbox_repository: InboxRepository,
        follower_repository: PodcastFollowerRepository,
        *,
        queue_manager: Optional["QueueManager"] = None,
        podcast_repository: Optional["SqlitePodcastRepository"] = None,
    ) -> "InboxService":
        """Builder that pulls ``seed_on_follow_count`` from ``Config``.

        When ``queue_manager`` and ``podcast_repository`` are supplied, delivered
        episodes are auto-submitted for the full pipeline (URL-optimized via the
        configured transcription provider).
        """
        return cls(
            inbox_repository,
            follower_repository,
            seed_on_follow_count=config.inbox_seed_on_follow,
            queue_manager=queue_manager,
            podcast_repository=podcast_repository,
            transcription_provider=getattr(config, "transcription_provider", ""),
        )

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def fanout_on_publish(self, episode_id: str, podcast_id: str) -> int:
        """
        Insert one inbox row per current follower of ``podcast_id``.

        Idempotent: re-running for the same episode is a no-op because the
        repository inserts with ``OR IGNORE`` on ``(user_id, episode_id)``.

        Returns the number of rows actually inserted.
        """
        follower_ids = self._followers.get_follower_user_ids(podcast_id)
        if not follower_ids:
            logger.debug("inbox_fanout_no_followers", episode_id=episode_id, podcast_id=podcast_id)
            return 0

        now = datetime.now(timezone.utc)
        entries = [
            InboxEntry(
                user_id=user_id,
                episode_id=episode_id,
                source="follow_new",
                state="unread",
                delivered_at=now,
            )
            for user_id in follower_ids
        ]
        inserted = self._repository.insert_many(entries)
        logger.info(
            "inbox_delivered",
            episode_id=episode_id,
            podcast_id=podcast_id,
            followers=len(follower_ids),
            inserted=inserted,
        )
        # A freshly-published episode is normally already enqueued by the
        # refresh-feed handler; this is the idempotent safety net so anything
        # that reaches an inbox is guaranteed a pipeline run.
        self._ensure_pipeline(
            lambda: self._podcasts.get_unqueued_unprocessed_episodes([episode_id]),
            source="follow_new",
        )
        return inserted

    def seed_on_follow(self, user_id: str, podcast_id: str) -> int:
        """
        Deliver up to ``seed_on_follow_count`` recent published episodes to
        the user's inbox with ``source='follow_seed'``.

        The repository returns candidates newest-first by ``pub_date``
        (RSS air date, not pipeline-completion time). We deliver them
        oldest-first with monotonically increasing ``delivered_at`` so
        that a single follow event lands as a chronological run in the
        inbox: the most recent episode sits at the top.

        Returns the number of rows actually inserted (may be 0 if the
        podcast has no published episodes yet, or if all the candidates were
        already delivered to this user via some prior path).
        """
        if self._seed_count == 0:
            return 0

        # Kick off transcription of the recent backlog FIRST, independently of
        # inbox delivery below. A brand-new podcast has no published episodes yet
        # (publish only happens once the pipeline finishes), so delivery is a
        # no-op on first follow — but the backlog still needs to start
        # processing. Selecting by air date (pub_date) is what a listener means
        # by "transcribe the last few episodes I just subscribed to". As each
        # episode completes, ``fanout_on_publish`` delivers it to this inbox.
        self._ensure_pipeline(
            lambda: self._podcasts.get_recent_unqueued_unprocessed_episodes(podcast_id, self._seed_count),
            source="follow_seed",
        )

        episode_ids = self._repository.recent_published_episode_ids(podcast_id, self._seed_count)
        if not episode_ids:
            logger.debug("inbox_seed_no_published_episodes", user_id=user_id, podcast_id=podcast_id)
            return 0

        ordered = list(reversed(episode_ids))
        base = datetime.now(timezone.utc)
        entries = [
            InboxEntry(
                user_id=user_id,
                episode_id=episode_id,
                source="follow_seed",
                state="unread",
                delivered_at=base + timedelta(milliseconds=i),
            )
            for i, episode_id in enumerate(ordered)
        ]
        inserted = self._repository.insert_many(entries)
        logger.info(
            "inbox_seeded",
            user_id=user_id,
            podcast_id=podcast_id,
            candidates=len(episode_ids),
            inserted=inserted,
        )
        return inserted

    def backfill_existing_followers(self, *, dry_run: bool = False) -> int:
        """
        One-time backfill: seed every current follower's inbox with the
        podcast's most-recent published episodes.

        Implemented as a single SQL statement (one round trip) so the cost
        is independent of the (podcasts × followers) cross-product. Use
        ``dry_run=True`` to count what would be delivered without writing.
        """
        inserted = self._repository.backfill_existing_followers(self._seed_count, dry_run=dry_run)
        logger.info(
            "inbox_backfill_existing_followers",
            seed_count=self._seed_count,
            inserted=inserted,
            dry_run=dry_run,
        )
        return inserted

    def _ensure_pipeline(self, lookup: "Callable[[], List]", *, source: str) -> int:
        """Run ``lookup`` for ``(episode_id, audio_url)`` orphans and enqueue them.

        Best-effort transcription submission shared by both delivery paths; they
        differ only in which repository query selects the pending set:

        - ``fanout_on_publish`` filters specific delivered episodes
          (``get_unqueued_unprocessed_episodes``) — usually none, since the
          refresh handler already enqueued them; this is the idempotent net.
        - ``seed_on_follow`` selects the podcast's recent backlog by air date
          (``get_recent_unqueued_unprocessed_episodes``) regardless of
          inbox-publish state, so a brand-new podcast still starts processing.

        No-op when the optional queue/podcast plumbing is absent. ``lookup`` is
        invoked only after that guard, so it may dereference those deps.
        """
        if not (self._queue and self._podcasts):
            return 0
        try:
            pending = lookup()
        except Exception:
            logger.exception("inbox_pipeline_lookup_failed", source=source)
            return 0
        return self._enqueue_pipeline(pending, source=source)

    def _enqueue_pipeline(self, pending: List, *, source: str) -> int:
        """Enqueue the URL-optimized full pipeline for ``(episode_id, audio_url)``
        pairs. Best-effort and idempotent (``enqueue_full_pipeline`` coalesces
        against in-flight tasks). Returns the number actually enqueued.
        """
        enqueued = 0
        for episode_id, audio_url in pending:
            try:
                if self._queue.enqueue_full_pipeline(
                    episode_id=episode_id,
                    audio_url=audio_url,
                    transcription_provider=self._transcription_provider,
                    initiated_by=f"inbox-{source}",
                ):
                    enqueued += 1
            except Exception:
                logger.exception("inbox_pipeline_enqueue_failed", episode_id=episode_id, source=source)
        if enqueued:
            logger.info("inbox_pipeline_enqueued", source=source, count=enqueued)
        return enqueued

    def mark_state(self, user_id: str, episode_id: str, state: str) -> InboxEntry:
        """
        Set ``state`` on the row for ``(user_id, episode_id)``.

        Raises:
            InvalidInboxStateError: if ``state`` is not a recognized value.
            InboxEntryNotFoundError: if no row exists for the pair.
        """
        if state not in INBOX_STATES:
            raise InvalidInboxStateError(f"Invalid state: {state!r} (expected one of {INBOX_STATES})")
        now = datetime.now(timezone.utc)
        entry = self._repository.update_state(user_id, episode_id, state, now)
        if entry is None:
            raise InboxEntryNotFoundError(f"No inbox row for user_id={user_id!r}, episode_id={episode_id!r}")
        logger.info("inbox_state_changed", user_id=user_id, episode_id=episode_id, state=state)
        return entry

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def list(
        self,
        user_id: str,
        *,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[datetime] = None,
    ) -> List[InboxItem]:
        """
        Return paginated inbox items, newest first.

        When ``state`` is None, dismissed rows are filtered out — the inbox
        is a triage view, not an audit log. Pass ``state='dismissed'``
        explicitly to list dismissals.
        """
        if state is not None and state not in INBOX_STATES:
            raise InvalidInboxStateError(f"Invalid state filter: {state!r} (expected one of {INBOX_STATES})")
        return self._repository.list_items(user_id, state=state, limit=limit, before=before)

    def unread_count(self, user_id: str) -> int:
        """Return the number of unread rows for ``user_id``."""
        return self._repository.unread_count(user_id)
