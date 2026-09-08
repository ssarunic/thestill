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
"""Abstract repository for per-user remote MCP tokens (spec #78 Phase 2).

One row per user, replaced in place on rotate. The two hot operations are
the guard's indexed lookup by hash (``find_active``) and the throttled
last-use bump (``touch_last_used``), whose once-a-minute rule is a SQL
predicate rather than caller-side state so it holds across instances.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from ..models.mcp_token import McpToken


class McpTokenRepository(ABC):
    """Abstract repository for ``mcp_tokens``."""

    @abstractmethod
    def get(self, user_id: str) -> Optional[McpToken]:
        """The user's row in any state (active, expired, revoked), or None."""

    @abstractmethod
    def upsert(self, token: McpToken) -> McpToken:
        """Create or rotate: replace the user's single row in one statement.

        Every column is overwritten, so a rotate resets ``revoked_at`` and
        the last-use fields as well as the hash.
        """

    @abstractmethod
    def find_active(self, token_hash: str, *, now: datetime) -> Optional[McpToken]:
        """Row matching ``token_hash`` that is neither revoked nor expired."""

    @abstractmethod
    def revoke(self, user_id: str, *, revoked_at: datetime) -> bool:
        """Set ``revoked_at``; True when an unrevoked row existed."""

    @abstractmethod
    def touch_last_used(self, user_id: str, *, now: datetime, ip: Optional[str], min_interval_seconds: int) -> bool:
        """Bump ``last_used_at``/``last_used_ip`` unless bumped within
        ``min_interval_seconds``. Returns True when a write happened."""
