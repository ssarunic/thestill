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

One row per user and episode on the logical (transcript) timeline. Writes
are ordered by the writing tab's ``session_id`` + ``seq`` rather than by
client clocks; ``updated_at`` is always server time.
"""

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

ListeningEvent = Literal["tick", "pause", "seek", "ended", "stop", "mark_played", "mark_unplayed"]

# Manual marks bypass write ordering: a click always wins over whatever the
# player stored (spec #90 "Mark as played").
MANUAL_EVENTS = frozenset({"mark_played", "mark_unplayed"})

# A pause/stop/page-hide write with no more than this many seconds left
# completes the episode — the usual length of an outro ad read.
COMPLETION_TAIL_SECONDS = 45.0

# Positions beyond ``duration + slack`` are clamped to the duration.
CLAMP_SLACK_SECONDS = 5.0

# Writes the player sends only while audio plays (spec #103 §4a): ``pause``
# and ``ended`` follow playback, ``tick`` fires during it. A ``stop`` is
# also sent for paused and restored sessions, so it is not one of them.
PLAYBACK_EVENTS = frozenset({"tick", "pause", "ended"})


class ListeningProgress(BaseModel):
    """A stored ``user_episode_progress`` row."""

    user_id: str
    episode_id: str
    position_seconds: float
    duration_seconds: Optional[float] = None
    completed_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    session_id: str
    seq: int
    updated_at: datetime
    # Spec #103: the last write that came from playback, for analytics only;
    # never in a summary. On a row planned for a write it is the time this
    # write sets, or None to keep the stored value; the repository applies
    # it only while the user's usage-analytics switch is on. A completion
    # counts as listened when it equals completed_at.
    listened_at: Optional[datetime] = None

    def summary(self) -> "ListeningSummary":
        return ListeningSummary(
            position_seconds=self.position_seconds,
            duration_seconds=self.duration_seconds,
            completed_at=self.completed_at,
            updated_at=self.updated_at,
        )


class ListeningSummary(BaseModel):
    """The public view of a row: what read payloads carry as ``listening``.

    ``session_id``, ``seq`` and ``stopped_at`` are write-ordering and restore
    bookkeeping; they never leave the server.
    """

    position_seconds: float
    duration_seconds: Optional[float] = None
    completed_at: Optional[datetime] = None
    updated_at: datetime

    @classmethod
    def from_columns(
        cls,
        position_seconds: Optional[float],
        duration_seconds: Optional[float],
        completed_at: Optional[datetime],
        updated_at: Optional[datetime],
    ) -> Optional["ListeningSummary"]:
        """Build from LEFT JOINed columns; None when the join found no row.

        ``updated_at`` is the NOT NULL sentinel — a position of 0 is a real
        value (completed or marked unplayed).
        """
        if updated_at is None:
            return None
        return cls(
            position_seconds=float(position_seconds or 0.0),
            duration_seconds=duration_seconds,
            completed_at=completed_at,
            updated_at=updated_at,
        )


class ProgressWrite(BaseModel):
    """One client write, as received by the service."""

    event: ListeningEvent
    position_seconds: float = 0.0
    duration_seconds: Optional[float] = None
    session_id: str
    seq: int


class WriteOutcome(BaseModel):
    """Result of :meth:`ListeningProgressService.record`.

    ``listening`` is the stored row after the write — the new one when
    accepted, the newer one that won when rejected — so the client cache
    only ever holds server-stamped state.
    """

    accepted: bool
    completed: bool
    listening: Optional[ListeningSummary] = None


class ResumableEpisode(BaseModel):
    """An unfinished episode with enough data to build a player track."""

    listening: ListeningSummary
    episode: Dict[str, Any]
