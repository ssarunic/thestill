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

"""Spec #64 — orchestration for claiming the legacy local account.

Thin wrapper over :class:`LegacyClaimRepository`. Two entry points:

- ``claim_for_new_user`` — called from the OAuth callback route when a
  brand-new real user was just created. Best-effort and NEVER raises
  (mirrors ``maybe_infer_region``): a claim failure must not turn into a
  failed login. Gated purely on the local row's existence — a successful
  claim deletes it, so this self-limits to (effectively) the first login.
- ``claim_by_cli`` / ``discard_by_cli`` — the operator-driven paths behind
  ``thestill claim-local-user`` for databases where real users already
  exist (the auto path never fires for them).
"""

from structlog import get_logger

from ..models.user import User
from ..repositories.legacy_claim_repository import LegacyClaimRepository, LegacyClaimResult
from ..repositories.user_repository import UserRepository
from .auth_service import DEFAULT_USER_EMAIL

logger = get_logger(__name__)


class LegacyClaimError(Exception):
    """CLI-facing error: the claim target cannot be resolved."""


class LegacyClaimService:
    def __init__(self, legacy_claim_repository: LegacyClaimRepository, user_repository: UserRepository):
        self._repo = legacy_claim_repository
        self._users = user_repository

    def claim_for_new_user(self, new_user: User) -> LegacyClaimResult:
        """Best-effort claim for a just-created real user. Never raises."""
        try:
            result = self._repo.claim_local_account(local_email=DEFAULT_USER_EMAIL, target_user_id=new_user.id)
            if result.claimed:
                logger.info(
                    "legacy_account_auto_claimed",
                    target_user_id=new_user.id,
                    counts=result.counts,
                )
            return result
        except Exception:
            logger.exception("legacy_account_auto_claim_failed", target_user_id=new_user.id)
            return LegacyClaimResult(found=True, claimed=False)

    def claim_by_cli(self, *, to_email: str, dry_run: bool = False) -> LegacyClaimResult:
        """Operator-driven claim. Resolves the target before any transaction."""
        if to_email == DEFAULT_USER_EMAIL:
            raise LegacyClaimError("Cannot claim the local account into itself")
        target = self._users.get_by_email(to_email)
        if target is None:
            raise LegacyClaimError(f"No user found with email {to_email!r} — they must log in at least once first")
        return self._repo.claim_local_account(local_email=DEFAULT_USER_EMAIL, target_user_id=target.id, dry_run=dry_run)

    def discard_by_cli(self, *, dry_run: bool = False) -> LegacyClaimResult:
        """Operator-driven discard (delete without transfer)."""
        return self._repo.discard_local_account(local_email=DEFAULT_USER_EMAIL, dry_run=dry_run)
