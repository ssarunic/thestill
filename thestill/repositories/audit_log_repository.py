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

"""Spec #104 — the append-only admin audit log (``admin_audit_log``).

There is deliberately no update or delete: the log records every admin
action and nothing an admin does can rewrite it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class AuditEntry:
    action: str
    actor: str  # web | cli | system
    created_at: datetime
    actor_user_id: Optional[str] = None
    target_user_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None


class AuditLogRepository(ABC):
    @abstractmethod
    def append(self, entry: AuditEntry) -> None:
        """Write one entry. ``details`` must be JSON-serialisable and never
        hold secrets or tokens."""

    @abstractmethod
    def list(
        self,
        *,
        action: Optional[str] = None,
        target_user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[AuditEntry]:
        """Newest first, optionally filtered."""

    @abstractmethod
    def count(self, *, action: Optional[str] = None, target_user_id: Optional[str] = None) -> int:
        """How many entries match the filters (for pagination)."""
