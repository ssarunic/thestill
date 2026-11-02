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

"""Abstract repository for the transcript editor's op log (spec #110 §2).

Two tables: ``transcript_edits`` (append-only; rows are never updated or
deleted except by the episode cascade) and ``transcript_edit_refreshes``
(the last ``seq`` each episode's downstream refresh was queued for). The
one rule kept in SQL is the append's optimistic check: a row is written
only when the episode's last ``seq`` is the one the writer saw, so two
editors can't interleave ops against different base states.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple

from ..models.transcript_edits import TranscriptEdit


class TranscriptEditRepository(ABC):
    @abstractmethod
    def append(self, edit: TranscriptEdit, *, expected_last_seq: int) -> Optional[TranscriptEdit]:
        """Append ``edit`` with ``seq = expected_last_seq + 1``.

        Returns the stored row, or ``None`` when the episode's last seq is
        no longer ``expected_last_seq`` (another writer got there first).
        ``edit.seq`` is ignored; the stored row carries the assigned one.
        """

    @abstractmethod
    def list_for_episode(self, episode_id: str) -> List[TranscriptEdit]:
        """Every row for the episode in ``seq`` order (empty for unknown ids)."""

    @abstractmethod
    def last_seq(self, episode_id: str) -> int:
        """The episode's highest ``seq``, 0 when it has no rows. One indexed query."""

    @abstractmethod
    def get(self, edit_id: str) -> Optional[TranscriptEdit]:
        """One row by id."""

    @abstractmethod
    def list_by_actor(self, actor_user_id: str, *, since: datetime) -> List[TranscriptEdit]:
        """Rows one user wrote at or after ``since``, oldest first."""

    @abstractmethod
    def episodes_with_edits(self) -> List[Tuple[str, int, datetime]]:
        """``(episode_id, last_seq, newest_created_at)`` for every episode with rows."""

    @abstractmethod
    def mark_refreshed(self, episode_id: str, *, seq: int, at: datetime) -> None:
        """Record that a downstream refresh covering ops up to ``seq`` was queued."""

    @abstractmethod
    def episodes_pending_refresh(self, *, idle_before: datetime) -> List[Tuple[str, int]]:
        """``(episode_id, last_seq)`` for episodes whose newest op is newer than
        their last queued refresh and was written at or before ``idle_before``."""
