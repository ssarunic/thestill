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

"""Spec #64 — atomic transfer/discard of the legacy local account's data.

The single-user identity (``local@thestill.me``) owns per-user rows
accumulated before a deployment switched to multi-user mode:
``podcast_followers``, ``user_episode_inbox``, ``user_briefings`` (with
``briefing_deliveries`` hanging off them by ``briefing_id``), and
``user_briefing_schedules``. This repository moves that data onto a real
account — or discards it — as ONE transaction, row-locked on the local
``users`` row, so concurrent callers (two racing first logins, a login
racing the CLI) resolve to exactly one winner; the loser observes
``found=False`` after unblocking. Nothing partially commits: a crash
mid-operation rolls back to the untouched pre-call state, so any retry
(another login, or the CLI) is always safe.

Deleting the local ``users`` row is the durable "already claimed" marker:
``ON DELETE CASCADE`` sweeps whatever rows deliberately stayed behind
(follower/inbox collisions, a schedule the target already had), and
single-user mode simply recreates the row fresh if the operator ever
reverts (``AuthService.get_or_create_default_user``).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class LegacyClaimResult:
    """Outcome of a claim/discard attempt.

    ``counts`` reports per-table row counts: for ``dry_run`` the rows the
    local account currently owns; for a real claim the rows actually
    moved (conflict-skipped rows are counted by the difference and then
    cascade-deleted with the local row).
    """

    found: bool  # the local account row existed at call time
    claimed: bool  # this call moved/deleted it (False for dry-run / not-found)
    counts: Dict[str, int] = field(default_factory=dict)


class LegacyClaimRepository(ABC):
    """Spec #64 — one-transaction claim/discard of the local account."""

    @abstractmethod
    def claim_local_account(self, *, local_email: str, target_user_id: str, dry_run: bool = False) -> LegacyClaimResult:
        """Transfer the local account's per-user data to ``target_user_id``.

        Within one row-locked transaction: reassign ``podcast_followers``
        and ``user_episode_inbox`` (skipping rows the target already has),
        reassign ``user_briefings`` (deliveries ride along via
        ``briefing_id``), move ``user_briefing_schedules`` only if the
        target has none, grant ``is_admin`` to the target, then delete the
        local ``users`` row. ``dry_run=True`` only counts — no writes.

        Returns ``found=False`` when no local account exists (steady state
        after a successful claim).
        """

    @abstractmethod
    def discard_local_account(self, *, local_email: str, dry_run: bool = False) -> LegacyClaimResult:
        """Delete the local account row; FK cascade removes all its
        per-user rows. ``dry_run=True`` only counts what would go."""
