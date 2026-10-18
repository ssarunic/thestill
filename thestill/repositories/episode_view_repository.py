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

"""Abstract repository for first episode opens (spec #103 Phase 1).

One fact per user and episode: when they first opened it. The inbox can't
hold it (a row exists only for delivered episodes, and its state is
overwritten), and analytics reads it instead of inbox ``read``. Written
only for readers who allow usage analytics (decision D5).
"""

from abc import ABC, abstractmethod
from datetime import datetime


class EpisodeViewRepository(ABC):
    """Abstract repository for ``user_episode_views``."""

    @abstractmethod
    def record_view(self, user_id: str, episode_id: str, at: datetime) -> bool:
        """Store ``at`` as the first view unless one exists. An unknown or
        malformed episode stores nothing, and so does a user whose
        ``usage_analytics_enabled`` is off when the statement runs, checked
        so that it serializes with ``UserRepository.update_usage_analytics``
        (an in-flight view can't outlive the opt-out's erase). Returns True
        when a row was added."""
