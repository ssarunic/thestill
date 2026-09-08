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
Per-user remote MCP capability token (spec #78 Phase 2).

One row per user. The plaintext token is never stored: the guard hashes
the path segment it receives and looks the hash up. ``token_prefix`` is the
only fragment kept in the clear, for the masked Settings display.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from pydantic import BaseModel, Field

# Scopes only ever narrow the owner's web-session ceiling. ``read`` is
# implicit and locked on; ``follows`` is opt-in; ``pipeline`` is offered to
# admins only and re-checked against ``User.is_admin`` on every request.
SCOPE_READ = "read"
SCOPE_FOLLOWS = "follows"
SCOPE_PIPELINE = "pipeline"
ALL_SCOPES: Tuple[str, ...] = (SCOPE_READ, SCOPE_FOLLOWS, SCOPE_PIPELINE)


class McpToken(BaseModel):
    """A user's remote MCP token row (spec #78 Phase 2)."""

    user_id: str
    token_hash: str
    token_prefix: str
    scopes: Tuple[str, ...] = (SCOPE_READ,)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    last_used_ip: Optional[str] = None
    revoked_at: Optional[datetime] = None

    def is_active(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now
