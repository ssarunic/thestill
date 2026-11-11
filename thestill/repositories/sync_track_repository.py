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
"""Abstract repository for sync tracks (spec #116).

One row per stored sync track. An episode has at most one ``current``
track, the one that describes its raw transcript, and at most one
``pending`` track, built from audio whose transcript is not stored yet.
Readers only ever see the current one. The file itself lives in file
storage at ``path``; this table says which file belongs to which
transcript.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

STATE_PENDING = "pending"
STATE_CURRENT = "current"


@dataclass(frozen=True)
class SyncTrackRecord:
    """Where one sync track is stored and what it was cut from."""

    episode_id: str
    generation: str
    state: str
    path: str  # file-storage key
    source: str  # local | dalston | realign
    frames: int
    anchors: int
    audio_bytes: int
    audio_duration_s: float
    created_at: Optional[datetime] = None


class SyncTrackRepository(ABC):
    """Abstract repository for ``episode_sync_tracks``."""

    @abstractmethod
    def put_pending(self, record: SyncTrackRecord) -> Optional[str]:
        """Store ``record`` as the episode's pending track.

        Replaces an earlier pending track of the same episode and returns
        that track's path so the caller can delete the file, or None."""

    @abstractmethod
    def promote(self, episode_id: str, generation: str) -> Optional[List[str]]:
        """Make the pending track ``generation`` the episode's current one.

        The previous current track's row is removed in the same transaction.
        Returns the paths of removed tracks (possibly empty), or None when
        there is no pending track with that generation (nothing changes)."""

    @abstractmethod
    def retire(self, episode_id: str, state: Optional[str] = None) -> List[str]:
        """Remove the episode's tracks (only ``state`` when given) and
        return their paths."""

    @abstractmethod
    def get(self, episode_id: str, state: str = STATE_CURRENT) -> Optional[SyncTrackRecord]:
        """The episode's track in ``state``, or None."""
