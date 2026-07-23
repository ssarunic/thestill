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

"""Spec #64 — ``LegacyClaimService`` orchestration.

The atomic move itself is contract-tested against real databases in
``tests/integration/test_legacy_claim_repository_contract.py``; this file
covers the service-layer contracts: target resolution, self-claim guard,
and the never-raises promise of the login-time path.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from thestill.repositories.legacy_claim_repository import LegacyClaimResult
from thestill.services.auth_service import DEFAULT_USER_EMAIL
from thestill.services.legacy_claim_service import LegacyClaimError, LegacyClaimService


def _service(claim_result=None, target_user=None):
    repo = MagicMock()
    if claim_result is not None:
        repo.claim_local_account.return_value = claim_result
        repo.discard_local_account.return_value = claim_result
    users = MagicMock()
    users.get_by_email.return_value = target_user
    return LegacyClaimService(repo, users), repo, users


def test_claim_for_new_user_passes_through_result():
    result = LegacyClaimResult(found=True, claimed=True, counts={"followers": 3})
    svc, repo, _ = _service(claim_result=result)
    user = SimpleNamespace(id="real-1", email="real@example.com")

    assert svc.claim_for_new_user(user) is result
    repo.claim_local_account.assert_called_once_with(local_email=DEFAULT_USER_EMAIL, target_user_id="real-1")


def test_claim_for_new_user_never_raises():
    svc, repo, _ = _service()
    repo.claim_local_account.side_effect = RuntimeError("db down")

    result = svc.claim_for_new_user(SimpleNamespace(id="real-1", email="real@example.com"))

    assert result.claimed is False  # swallowed, reported as not-claimed


def test_claim_by_cli_resolves_target_before_transaction():
    result = LegacyClaimResult(found=True, claimed=True)
    target = SimpleNamespace(id="real-2", email="real@example.com")
    svc, repo, users = _service(claim_result=result, target_user=target)

    assert svc.claim_by_cli(to_email="real@example.com") is result
    users.get_by_email.assert_called_once_with("real@example.com")
    repo.claim_local_account.assert_called_once_with(
        local_email=DEFAULT_USER_EMAIL, target_user_id="real-2", dry_run=False
    )


def test_claim_by_cli_unknown_target_raises_before_repo_call():
    svc, repo, _ = _service(target_user=None)
    with pytest.raises(LegacyClaimError):
        svc.claim_by_cli(to_email="nobody@example.com")
    repo.claim_local_account.assert_not_called()


def test_claim_by_cli_refuses_self_claim():
    svc, repo, _ = _service()
    with pytest.raises(LegacyClaimError):
        svc.claim_by_cli(to_email=DEFAULT_USER_EMAIL)
    repo.claim_local_account.assert_not_called()


def test_discard_by_cli_threads_dry_run():
    result = LegacyClaimResult(found=True, claimed=False, counts={"followers": 2})
    svc, repo, _ = _service(claim_result=result)

    assert svc.discard_by_cli(dry_run=True) is result
    repo.discard_local_account.assert_called_once_with(local_email=DEFAULT_USER_EMAIL, dry_run=True)
