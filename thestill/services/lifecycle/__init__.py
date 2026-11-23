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

"""Lifecycle messages: welcome and product-tips email (spec #118)."""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ...repositories.factory import Repositories
    from ...utils.config import Config
    from ..abuse.settings_service import SettingsService
    from ..email_sender import EmailSender
    from .service import LifecycleEmailService


def build_lifecycle_email_service(
    config: "Config",
    repos: "Repositories",
    email_sender: "Optional[EmailSender]",
    settings: "SettingsService",
) -> "Optional[LifecycleEmailService]":
    """The service for this process, or None: lifecycle email needs
    multi-user mode (single-user has one operator, no signups) and an email
    sender. The ``lifecycle_email_enabled`` runtime switch is read on every
    tick, so the operator can pause and resume without a restart."""
    if not config.multi_user or email_sender is None:
        return None
    from .renderer import LifecycleEmailRenderer
    from .service import LifecycleEmailService

    return LifecycleEmailService(
        repos.user_message,
        LifecycleEmailRenderer(
            public_base_url=config.public_base_url,
            secret=config.unsubscribe_secret or config.jwt_secret_key,
        ),
        email_sender,
        enabled=lambda: bool(settings.get("lifecycle_email_enabled")),
        max_attempts=config.briefing_email_max_attempts,
        backoff_seconds=config.briefing_email_backoff_seconds,
    )
