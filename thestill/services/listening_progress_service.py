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
Per-user listening progress (spec #90).

The rules for turning one client write into a stored row — validation,
clamping, completion, Stop — are the pure :func:`plan_row`, testable without
a database. Write ordering is the repository's atomic upsert predicate. This
service orchestrates the two, marks the inbox row read on completion, and
hydrates unfinished rows with enough episode data to build a player track.

Positions are personal data: they are never logged above debug level.
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from structlog import get_logger

from ..models.listening import (
    CLAMP_SLACK_SECONDS,
    COMPLETION_TAIL_SECONDS,
    MANUAL_EVENTS,
    PLAYBACK_EVENTS,
    ListeningProgress,
    ListeningSummary,
    ProgressWrite,
    ResumableEpisode,
    WriteOutcome,
)
from ..models.podcast import AlternateEnclosure, Episode, Podcast
from ..repositories.listening_progress_repository import ListeningProgressRepository

# Not utils.postgres_ext: psycopg is an optional extra, and this service also
# runs on SQLite-only installs.
from ..utils.ids import is_uuid
from .playback import build_track_payload

logger = get_logger(__name__)

# The Continue listening strip shows a handful; the API caps the page.
MAX_IN_PROGRESS_LIMIT = 20

# The Continue listening list shows what you are listening to now, not every
# episode you ever started: an unfinished row drops out after this long
# without a write. It keeps its position (resume, inbox rows, restore).
CONTINUE_LISTENING_WINDOW = timedelta(days=3)

MAX_SESSION_ID_LENGTH = 64


class ListeningProgressError(Exception):
    """Base exception for listening progress errors."""


class InvalidProgressError(ListeningProgressError):
    """Raised for a write the service refuses (non-finite or negative position, bad session)."""


class ListeningEpisodeNotFoundError(ListeningProgressError):
    """Raised when a write or read targets an unknown episode."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _usable_duration(value: Optional[float]) -> Optional[float]:
    """Engine durations of 0, NaN or Infinity (streams) mean "unknown"."""
    if value is None or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def plan_row(
    write: ProgressWrite,
    *,
    user_id: str,
    episode_id: str,
    stored: Optional[ListeningProgress],
    now: datetime,
    measure: bool = False,
) -> ListeningProgress:
    """Turn one client write into the row to store.

    - ``mark_played`` completes at position 0; ``mark_unplayed`` clears the
      completion at position 0. Both ignore the written position.
    - Otherwise the position must be finite and non-negative; a position
      beyond ``duration + 5 s`` is clamped to the duration.
    - ``ended`` completes. ``pause`` and ``stop`` complete when no more than
      45 s are left of a known duration. ``tick`` and ``seek`` never do —
      a backgrounded tab still playing near the end is not finished.
      Completion resets the position to 0 so the next play starts at the top.
    - ``stop`` sets ``stopped_at`` (hides the row from restore); any other
      write clears it.

    The duration is the written one, else the stored one, so a write
    without a duration still applies the 45 s rule.

    ``listened_at`` (spec #103) becomes ``now`` for playback — ``tick``,
    ``pause``, ``ended`` and a completing ``stop`` — when ``measure`` (the
    user's usage-analytics consent, off unless the caller passes it);
    every other write carries the stored value forward, so "Mark as played
    / unplayed" never counts as listening.
    """
    if not write.session_id or len(write.session_id) > MAX_SESSION_ID_LENGTH:
        raise InvalidProgressError(f"session_id must be 1-{MAX_SESSION_ID_LENGTH} characters")
    if write.seq < 0:
        raise InvalidProgressError("seq must be non-negative")

    duration = _usable_duration(write.duration_seconds)
    if duration is None and stored is not None:
        duration = stored.duration_seconds

    if write.event == "mark_played":
        position, completed = 0.0, True
    elif write.event == "mark_unplayed":
        position, completed = 0.0, False
    else:
        position = write.position_seconds
        if not math.isfinite(position) or position < 0:
            raise InvalidProgressError("position_seconds must be a finite, non-negative number")
        if duration is not None and position > duration + CLAMP_SLACK_SECONDS:
            position = duration
        near_end = duration is not None and duration - position <= COMPLETION_TAIL_SECONDS
        completed = write.event == "ended" or (write.event in ("pause", "stop") and near_end)
        if completed:
            position = 0.0

    playback = write.event in PLAYBACK_EVENTS or (write.event == "stop" and completed)
    listened_at = now if measure and playback else (stored.listened_at if stored else None)

    return ListeningProgress(
        user_id=user_id,
        episode_id=episode_id,
        position_seconds=position,
        duration_seconds=duration,
        completed_at=now if completed else None,
        stopped_at=now if write.event == "stop" else None,
        session_id=write.session_id,
        seq=write.seq,
        updated_at=now,
        listened_at=listened_at,
    )


class EpisodeLookups(Protocol):
    """The podcast-repository reads this service needs (both backends provide them)."""

    def get_episode(self, episode_id: str) -> Optional[Tuple[Podcast, Episode]]: ...

    def get_episodes_by_ids(self, episode_ids: Sequence[str]) -> Dict[str, Tuple[Podcast, Episode]]: ...

    def get_alternate_enclosures_for_episodes(self, episode_ids: List[str]) -> Dict[str, List[AlternateEnclosure]]: ...


class ListeningProgressService:
    """Record, read and list per-user listening progress."""

    def __init__(
        self,
        repository: ListeningProgressRepository,
        podcast_repository: EpisodeLookups,
        mark_read: Callable[[str, str], bool],
        clock: Callable[[], datetime] = _utcnow,
    ):
        """
        Args:
            repository: The ``user_episode_progress`` store.
            podcast_repository: Episode lookups (existence, hydration).
            mark_read: ``InboxService.mark_read`` — flips an unread inbox
                row to read; a quiet no-op for saved, dismissed or absent rows.
            clock: Injectable for tests.
        """
        self.repository = repository
        self.podcast_repository = podcast_repository
        self.mark_read = mark_read
        self.clock = clock

    def _require_episode(self, episode_id: str) -> None:
        # Validate the shape first: on Postgres a malformed id raises on the
        # uuid cast instead of matching nothing.
        if not is_uuid(episode_id) or self.podcast_repository.get_episode(episode_id) is None:
            raise ListeningEpisodeNotFoundError(episode_id)

    def record(self, user_id: str, episode_id: str, write: ProgressWrite, *, measure: bool = False) -> WriteOutcome:
        """Apply one client write. ``measure`` is the caller's usage-analytics
        consent for this request (``may_measure``); see :func:`plan_row`.
        Off by default, so a caller that forgets it records nothing.

        Raises:
            ListeningEpisodeNotFoundError: Unknown episode.
            InvalidProgressError: The write fails validation.
        """
        self._require_episode(episode_id)
        stored = self.repository.get(user_id, episode_id)
        row = plan_row(write, user_id=user_id, episode_id=episode_id, stored=stored, now=self.clock(), measure=measure)
        accepted = self.repository.upsert_if_newer(row, force=write.event in MANUAL_EVENTS)

        if not accepted:
            logger.debug(
                "listening_write_rejected",
                user_id=user_id,
                episode_id=episode_id,
                seq=write.seq,
                stored_seq=stored.seq if stored else None,
            )
            current = self.repository.get(user_id, episode_id)
            return WriteOutcome(accepted=False, completed=False, listening=current.summary() if current else None)

        completed = row.completed_at is not None
        if completed:
            logger.info("listening_completed", user_id=user_id, episode_id=episode_id, trigger=write.event)
            try:
                self.mark_read(user_id, episode_id)
            except Exception:
                # The position is stored; a failed inbox flip must not fail
                # the write, but it must not be silent either.
                logger.warning("listening_mark_read_failed", user_id=user_id, episode_id=episode_id, exc_info=True)
        return WriteOutcome(accepted=True, completed=completed, listening=row.summary())

    def get_summary(self, user_id: str, episode_id: str) -> Optional[ListeningSummary]:
        """The user's progress on an episode, or None when never played."""
        row = self.repository.get(user_id, episode_id)
        return row.summary() if row else None

    def latest(self, user_id: str) -> Optional[ResumableEpisode]:
        """The restore candidate: the most recently updated unfinished, unstopped row."""
        items = self._hydrate(self.repository.list_unfinished(user_id, limit=1, exclude_stopped=True))
        return items[0] if items else None

    def list_in_progress(self, user_id: str, limit: int) -> List[ResumableEpisode]:
        """Unfinished episodes written in the last 3 days, newest first (Stop does not hide them)."""
        limit = max(1, min(limit, MAX_IN_PROGRESS_LIMIT))
        since = self.clock() - CONTINUE_LISTENING_WINDOW
        return self._hydrate(
            self.repository.list_unfinished(user_id, limit=limit, exclude_stopped=False, updated_since=since)
        )

    def _hydrate(self, rows: List[ListeningProgress]) -> List[ResumableEpisode]:
        if not rows:
            return []
        ids = [r.episode_id for r in rows]
        found = self.podcast_repository.get_episodes_by_ids(ids)
        alternates = self.podcast_repository.get_alternate_enclosures_for_episodes([i for i in ids if i in found])
        items: List[ResumableEpisode] = []
        for row in rows:
            pair = found.get(row.episode_id)
            if pair is None:
                continue  # deleted between the two reads; the cascade removes the row
            podcast, episode = pair
            items.append(
                ResumableEpisode(
                    listening=row.summary(),
                    episode=build_track_payload(podcast, episode, alternates.get(row.episode_id, [])),
                )
            )
        return items
