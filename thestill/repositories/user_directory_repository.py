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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional, Sequence, Tuple

from ..models.user import User, UserStatus
from .usage_event_repository import COUNTED_SQL

UserSort = Literal["attention", "created", "last-login", "follows", "email"]

#: ORDER BY per sort, shared by both backends (the counts are select aliases).
#: ``attention`` puts the users most worth a look first (spec #104 §4.1):
#: quota hits, then feeds brought in, in the last 24 hours.
USER_SORT_ORDER = {
    "attention": "quota_exceeded_recent DESC, feed_introduced_recent DESC, u.created_at DESC",
    "created": "u.created_at DESC",
    "last-login": "u.last_login_at DESC NULLS LAST",
    "follows": "follows DESC, u.created_at DESC",
    "email": "u.email ASC",
}

#: Ledger kinds the listing counts per user since ``usage_since``.
LISTED_USAGE_KINDS = ("feed_introduced", "follow", "import", "translation", "quota_exceeded")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def summary_columns(placeholder: str, usage_since: Optional[datetime]) -> Tuple[str, list]:
    """The per-user count columns of a listing, with their parameters."""
    since = usage_since or _EPOCH
    cols = [
        "(SELECT COUNT(*) FROM podcast_followers f WHERE f.user_id = u.id) AS follows",
        "(SELECT COUNT(*) FROM suspended_follows s WHERE s.user_id = u.id) AS suspended",
        "(SELECT COUNT(*) FROM usage_events e WHERE e.user_id = u.id AND e.kind = 'feed_introduced'"
        " AND e.state = 'committed') AS feed_introduced_total",
    ]
    params: list = []
    for kind in LISTED_USAGE_KINDS:
        cols.append(
            f"(SELECT COUNT(*) FROM usage_events e WHERE e.user_id = u.id AND e.kind = '{kind}'"
            f" AND e.{COUNTED_SQL} AND e.created_at >= {placeholder}) AS {kind}_recent"
        )
        params.append(since)
    return ",\n                    ".join(cols), params


def listing_where(placeholder: str, status: Optional[UserStatus], q: Optional[str]) -> Tuple[str, list]:
    """The WHERE clause of a listing (status filter, email-or-name search)."""
    clauses, params = [], []
    if status is not None:
        clauses.append(f"u.status = {placeholder}")
        params.append(UserStatus(status).value)
    if q:
        clauses.append(
            f"(lower(u.email) LIKE {placeholder} ESCAPE '\\' OR lower(coalesce(u.name, '')) LIKE {placeholder} ESCAPE '\\')"
        )
        params.extend([search_pattern(q)] * 2)
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


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
    #: Counted ledger rows per kind since the listing's ``usage_since``.
    usage_recent: Dict[str, int] = field(default_factory=dict)
    #: Committed ``feed_introduced`` rows, all time (the ledger's record of
    #: feeds this user brought in; older follows aren't in it).
    feeds_brought_in_total: int = 0


def summary_from_row(row, user: User) -> UserSummary:
    return UserSummary(
        user=user,
        follows=row["follows"],
        suspended_follows=row["suspended"],
        usage_recent={kind: row[f"{kind}_recent"] for kind in LISTED_USAGE_KINDS},
        feeds_brought_in_total=row["feed_introduced_total"],
    )


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
        usage_since: Optional[datetime] = None,
    ) -> List[UserSummary]:
        """Users with their follow counts and ledger usage since
        ``usage_since``. ``q`` matches email or name, case-insensitively."""

    @abstractmethod
    def count_users(self, *, status: Optional[UserStatus] = None, q: Optional[str] = None) -> int:
        """How many users match (for pagination)."""

    @abstractmethod
    def created_since(self, since: datetime) -> List[User]:
        """Accounts created since ``since``, newest first (alerts, digest)."""

    @abstractmethod
    def emails_by_id(self, user_ids: Sequence[str]) -> Dict[str, str]:
        """``{user_id: email}`` for the ids that exist, in one query."""

    @abstractmethod
    def count_admins(self) -> int:
        """Active admins — so the last one can't be demoted or disabled."""
