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

"""Spec #104 Phase 2 — the per-user usage ledger (``usage_events``).

One row per costly action, written *before* it runs. ``reserve`` is the
atomic check-and-record a quota needs: under a per-(user, lock group)
lock it counts the user's ``reserved`` + ``committed`` rows in the window
and either inserts a ``reserved`` row or records a ``quota_exceeded``
refusal. The caller then settles the reservation ``committed`` (money
spent) or ``released`` (nothing spent).

Each backend module also exposes connection-level helpers so
``follow_with_gate`` can count and write ledger rows inside its own
transaction.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

USAGE_KINDS = ("feed_introduced", "follow", "import", "translation", "narration", "quota_exceeded")
#: Rows that count against a quota: a pending reservation counts too, so a
#: slow translation can't let a second one through.
COUNTED_STATES = ("reserved", "committed")
COUNTED_SQL = "state IN (" + ", ".join(f"'{state}'" for state in COUNTED_STATES) + ")"
#: Ledger rows kept past retention: they say who brought a feed in (§1.5).
PRUNE_KEEP_KINDS = ("feed_introduced",)
#: The follow quotas' lock group, taken by ``follow_with_gate``.
FOLLOW_LOCK_GROUP = "follow"


def usage_lock_key(user_id: str, lock_group: str) -> str:
    """Advisory-lock key for one user's quotas in one group. Every path
    takes these before any per-podcast lock, so follows can't deadlock."""
    return f"usage:{user_id}:{lock_group}"


@dataclass(frozen=True)
class UsageRecord:
    kind: str
    subject_id: Optional[str] = None
    state: str = "committed"


@dataclass(frozen=True)
class ReserveResult:
    #: The reserved row, or None when the quota refused.
    event_id: Optional[str]
    #: Counted rows in the window before this request.
    count: int
    #: When a refused request's allowance frees up: the counted row whose
    #: expiry brings the count back under the limit. None when allowed.
    frees_at: Optional[datetime]


class UsageEventRepository(ABC):
    @abstractmethod
    def reserve(
        self,
        user_id: str,
        kind: str,
        *,
        quota: str,
        limit: int,
        since: datetime,
        lock_group: str,
        subject_id: Optional[str],
        now: datetime,
    ) -> ReserveResult:
        """Atomically count and reserve. ``limit`` 0 means unlimited (the
        row is still recorded). On refusal a committed ``quota_exceeded``
        row (subject = ``quota``) is written instead, and ``frees_at`` says
        when enough rows will have left the window."""

    @abstractmethod
    def settle(self, event_id: str, state: str, *, now: datetime, subject_id: Optional[str] = None) -> bool:
        """Move a ``reserved`` row to ``committed`` or ``released``. A row
        already settled (e.g. expired by housekeeping) is left alone."""

    @abstractmethod
    def record(self, user_id: str, records: Sequence[UsageRecord], *, now: datetime) -> None:
        """Insert already-settled rows."""

    @abstractmethod
    def window(self, user_id: str, kind: str, since: datetime) -> Tuple[int, Optional[datetime]]:
        """Counted rows of ``kind`` since ``since``, and the oldest one's
        time (for a read-only precheck's ``Retry-After``)."""

    @abstractmethod
    def counts_by_kind(self, user_id: str, since: datetime) -> Dict[str, int]:
        """Counted rows per kind since ``since`` (kinds with none omitted)."""

    @abstractmethod
    def counts_by_user(self, kind: str, since: datetime) -> Dict[str, int]:
        """Counted rows of ``kind`` per user since ``since`` (alerts, digest)."""

    @abstractmethod
    def release_stale(self, older_than: datetime, *, now: datetime) -> int:
        """Release ``reserved`` rows created before ``older_than`` — a
        process died between reserve and settle. Returns how many."""

    @abstractmethod
    def prune(self, older_than: datetime, *, keep_kinds: Sequence[str] = PRUNE_KEEP_KINDS) -> int:
        """Delete settled rows older than ``older_than``, except ``keep_kinds``."""
