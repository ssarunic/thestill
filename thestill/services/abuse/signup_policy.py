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

"""Spec #104 §1.2 — the signup gate.

Checked only when a Google identity would create a *new* account, after the
account-linking lookup by email: an existing user is never locked out.
"""

from typing import Iterable

from structlog import get_logger

from .errors import SignupRefusedError
from .settings_service import SettingsService

logger = get_logger(__name__)


def evaluate_signup(mode: str, allowlist: Iterable[str], email: str) -> bool:
    """Whether ``email`` may create an account under ``mode``.

    Allowlist entries are an address (``alice@example.com``) or a domain
    (``@example.com``), compared case-insensitively.
    """
    if mode == "open":
        return True
    if mode != "allowlist":
        return False
    address = email.strip().lower()
    domain = "@" + address.rpartition("@")[2]
    entries = {entry.strip().lower() for entry in allowlist}
    return address in entries or domain in entries


class SignupPolicy:
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings

    def check(self, email: str) -> None:
        mode = self._settings.get("signup_mode")
        if evaluate_signup(mode, self._settings.get("signup_allowlist"), email):
            return
        # The domain only — never the address (no PII in logs).
        logger.warning("signup_refused", mode=mode, email_domain=email.rpartition("@")[2].lower())
        raise SignupRefusedError(mode)
