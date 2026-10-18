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

"""Spec #104 §1.6 — writing the admin audit log."""

from typing import TYPE_CHECKING, Any, Callable, Optional

from structlog import get_logger

from ...repositories.audit_log_repository import AuditEntry, AuditLogRepository
from ...utils.datetime_utils import now_utc
from .actor import Actor

if TYPE_CHECKING:
    from datetime import datetime

logger = get_logger(__name__)

#: Every action the log records (spec #104 §1.6).
AUDIT_ACTIONS = (
    "user.suspend",
    "user.ban",
    "user.reinstate",
    "user.logout_everywhere",
    "user.remove_introduced_feeds",
    "user.quota_override",  # Phase 2
    "admin.grant",
    "admin.revoke",
    "setting.set",
)


class AuditLog:
    def __init__(self, repository: AuditLogRepository, *, clock: Callable[[], "datetime"] = now_utc) -> None:
        self._repo = repository
        self._clock = clock

    def record(self, actor: Actor, action: str, *, target_user_id: Optional[str] = None, **details: Any) -> None:
        """Append one entry. ``details`` holds reasons and old/new values —
        never secrets or tokens. A system actor's name goes in details."""
        if action not in AUDIT_ACTIONS:
            raise ValueError(f"unknown audit action {action!r}")
        if actor.name:
            details = {"actor_name": actor.name, **details}
        self._repo.append(
            AuditEntry(
                action=action,
                actor=actor.kind,
                created_at=self._clock(),
                actor_user_id=actor.user_id,
                target_user_id=target_user_id,
                details=details,
            )
        )
        logger.info("admin_audit", action=action, actor=actor.kind, target_user_id=target_user_id)
