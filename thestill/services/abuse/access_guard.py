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

"""Spec #104 — the one seam costly services ask "may this go ahead?".

``ensure_active`` / ``ensure_user_active`` are the account-status checks:
no-ops in single-user mode (there is only the operator). ``require_feature``
is the runtime switch check, which applies in every mode because it
protects spend, not users.

Services take an optional guard and default to ``NULL_GUARD``, which allows
everything — hand-built test fixtures keep working, and production wiring
(``build_abuse_services``) always passes the real one.
"""

from typing import TYPE_CHECKING, Optional

from structlog import get_logger

from ...models.user import User
from .errors import AccountDisabledError

if TYPE_CHECKING:
    from ...repositories.user_repository import UserRepository
    from .settings_service import SettingsService

logger = get_logger(__name__)


class AccessGuard:
    def __init__(
        self,
        users: Optional["UserRepository"],
        settings: Optional["SettingsService"],
        *,
        multi_user: bool,
    ) -> None:
        self._users = users
        self._settings = settings
        self._multi_user = multi_user

    def ensure_active(self, user: User) -> None:
        """Raise ``AccountDisabledError`` for a suspended or banned user."""
        if self._multi_user and not user.is_active:
            raise AccountDisabledError(user.status.value)

    def ensure_user_active(self, user_id: str) -> None:
        """Fresh primary-key read, then ``ensure_active``. A missing user is
        not this check's business: callers already handle it."""
        if not self._multi_user or self._users is None:
            return
        user = self._users.get_by_id(user_id)
        if user is not None:
            self.ensure_active(user)

    def require_feature(self, feature: str) -> None:
        """Raise ``FeaturePausedError`` when ``feature``'s switch is off or
        ``read_only`` is on."""
        if self._settings is not None:
            self._settings.require(feature)


NULL_GUARD = AccessGuard(None, None, multi_user=False)
