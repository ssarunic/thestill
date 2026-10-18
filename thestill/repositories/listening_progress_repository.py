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
"""Abstract repository for per-user listening progress (spec #90).

A store for one table. Its single rule is the write-ordering predicate,
kept in SQL so accept-or-reject is one atomic statement: no read-then-write
race between two requests from the same tab. Every other rule (completion,
clamping, Stop) lives in ``ListeningProgressService``.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from ..models.listening import ListeningProgress


class ListeningProgressRepository(ABC):
    """Abstract repository for ``user_episode_progress``."""

    @abstractmethod
    def get(self, user_id: str, episode_id: str) -> Optional[ListeningProgress]:
        """The user's row for the episode, or None (also for malformed ids)."""

    @abstractmethod
    def upsert_if_newer(self, row: ListeningProgress, *, force: bool = False) -> bool:
        """Insert or replace the row unless the stored one is newer.

        A write replaces the stored row when ``force`` is set, when its
        ``session_id`` differs from the stored one (another tab or device:
        the latest action wins), or when the session matches and its
        ``seq`` is greater. ``duration_seconds`` keeps the stored value when
        the write carries none. Returns True when the row was written.

        ``listened_at`` (spec #103) only moves forward and is kept when the
        write carries none. It is written only if the user's
        ``usage_analytics_enabled`` is on when the statement runs, checked
        so that it serializes with ``UserRepository.update_usage_analytics``:
        a write in flight when the user opts out can't restore what the
        opt-out erased.
        """

    @abstractmethod
    def list_unfinished(
        self,
        user_id: str,
        *,
        limit: int,
        exclude_stopped: bool,
        updated_since: Optional[datetime] = None,
    ) -> List[ListeningProgress]:
        """Rows with a position and no completion, most recently updated first.

        ``exclude_stopped`` drops rows whose last write was a Stop — the
        restore candidate (``latest``) honours Stop; the Continue listening
        list does not. ``updated_since`` keeps only rows written at or after
        that instant (the list's recency window; restore has none).
        """
