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

"""Abstract repository for connector days (spec #111 §4).

One fact per reader and day: they made at least one successful remote MCP
(Claude connector) call. ``mcp_tokens.last_used_at`` keeps only the latest
use, so the Scorecard can't count connector days from it. Written only for
readers who allow usage analytics (spec #103 D5).
"""

from abc import ABC, abstractmethod
from datetime import datetime


class ConnectorDayRepository(ABC):
    """Abstract repository for ``user_connector_days``."""

    @abstractmethod
    def record_day(self, user_id: str, at: datetime) -> bool:
        """Store the reader-local day of ``at`` unless it is already stored.
        The day is taken in the timezone of the reader's briefing schedule,
        UTC without one. Stores nothing for an unknown or malformed user, or
        one whose ``usage_analytics_enabled`` is off when the statement runs
        (checked so that it serializes with
        ``UserRepository.update_usage_analytics``). Returns True when a row
        was added."""
