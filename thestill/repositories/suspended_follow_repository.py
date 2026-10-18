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

"""Spec #104 — a banned user's follows, moved aside (``suspended_follows``).

About 33 queries read ``podcast_followers`` to decide whether a feed has
followers. Rather than teach each one to skip banned users, a ban *moves*
the rows here, so the follower gate stops feeds only they followed. Both
moves are one transaction over the two tables and are idempotent.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable, List


class SuspendedFollowRepository(ABC):
    @abstractmethod
    def move_follows_aside(self, user_id: str, *, now: datetime) -> List[str]:
        """Move every ``podcast_followers`` row of ``user_id`` here, keeping
        ``created_at``. Fires no unfollow side effects; inbox rows stay.
        Returns the podcast ids moved."""

    @abstractmethod
    def restore_follows(self, user_id: str) -> List[str]:
        """Move the rows back with their original ``created_at``, bypassing
        the follow gate (an admin chose it). Returns the podcast ids restored."""

    @abstractmethod
    def discard(self, user_id: str, podcast_ids: Iterable[str]) -> int:
        """Drop moved-aside rows for good (remove-introduced-feeds)."""

    @abstractmethod
    def list_podcast_ids(self, user_id: str) -> List[str]:
        """Podcasts whose follow by ``user_id`` is currently moved aside."""
