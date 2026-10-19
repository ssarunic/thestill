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

"""Spec #104 — the operator's view across users (``thestill user list``,
the admin console). Cross-table aggregates live here, not in the core
``UserRepository``, so the per-request auth path stays lean.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Literal, Optional

from ..models.user import User, UserStatus

UserSort = Literal["created", "last-login", "follows"]

#: ORDER BY per sort, shared by both backends (``follows`` is a select alias).
USER_SORT_ORDER = {
    "created": "u.created_at DESC",
    "last-login": "u.last_login_at DESC NULLS LAST",
    "follows": "follows DESC, u.created_at DESC",
}


def search_pattern(q: str) -> str:
    """A ``LIKE ... ESCAPE '\\'`` pattern matching ``q`` anywhere, with its
    own ``%`` and ``_`` taken literally."""
    escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@dataclass(frozen=True)
class UserSummary:
    user: User
    #: Live rows in ``podcast_followers``.
    follows: int
    #: Rows moved aside by a ban.
    suspended_follows: int


class UserDirectoryRepository(ABC):
    @abstractmethod
    def list_users(
        self,
        *,
        status: Optional[UserStatus] = None,
        q: Optional[str] = None,
        sort: UserSort = "created",
        limit: int = 50,
        offset: int = 0,
    ) -> List[UserSummary]:
        """Users with their follow counts. ``q`` matches email or name,
        case-insensitively. Sorts are newest / most first."""

    @abstractmethod
    def count_users(self, *, status: Optional[UserStatus] = None, q: Optional[str] = None) -> int:
        """How many users match (for pagination)."""

    @abstractmethod
    def count_admins(self) -> int:
        """Active admins — so the last one can't be demoted or disabled."""
