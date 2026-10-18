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

"""Spec #104 — the one place the abuse-prevention services are built.

The web app, the CLI and the MCP server each call ``build_abuse_services``
instead of wiring by hand, so no entry point can forget the guard and fail
open.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from ...utils.datetime_utils import now_utc
from .access_guard import AccessGuard
from .account_admin_service import AccountAdminService
from .alert_channels import build_alert_channels
from .alerts import AlertService, AlertSignals
from .audit_service import AuditLog
from .follow_policy import FollowPolicy, NewFeedPrecheck
from .quota_service import QuotaService
from .rate_limit_hits import user_rate_limit_hits
from .settings_service import SettingsService
from .signup_policy import SignupPolicy

if TYPE_CHECKING:
    from ...repositories.factory import RepositoryBundle
    from ...utils.config import Config
    from ..email_sender import EmailSender


@dataclass
class AbuseServices:
    settings: SettingsService
    audit: AuditLog
    guard: AccessGuard
    signup: SignupPolicy
    follow_policy: FollowPolicy
    new_feed_precheck: NewFeedPrecheck
    account_admin: AccountAdminService
    quota: QuotaService
    alerts: AlertService


def build_abuse_services(
    config: "Config",
    repos: "RepositoryBundle",
    *,
    email_sender: Optional["EmailSender"] = None,
    clock: Callable[[], datetime] = now_utc,
) -> AbuseServices:
    """``email_sender`` adds the email alert channel (log-only without it).
    ``clock`` lets tests pin time for quota windows, admin actions and alerts."""

    audit = AuditLog(repos.audit_log, clock=clock)
    settings = SettingsService(repos.settings, config, audit)
    quota = QuotaService(repos.usage, repos.user, settings, config, clock=clock)
    return AbuseServices(
        settings=settings,
        audit=audit,
        guard=AccessGuard(repos.user, settings, multi_user=config.multi_user),
        signup=SignupPolicy(settings),
        follow_policy=FollowPolicy(settings, quota),
        new_feed_precheck=NewFeedPrecheck(settings, repos.follower, quota),
        account_admin=AccountAdminService(
            users=repos.user,
            directory=repos.user_directory,
            followers=repos.follower,
            suspended=repos.suspended_follow,
            mcp_tokens=repos.mcp_token,
            podcasts=repos.podcast,
            queue=repos.queue_manager,
            audit=audit,
            multi_user=config.multi_user,
            settings=settings,
            quota=quota,
            clock=clock,
        ),
        quota=quota,
        alerts=AlertService(
            signals=AlertSignals(
                usage=repos.usage,
                directory=repos.user_directory,
                queue=repos.queue_manager,
                rate_limit_hits=user_rate_limit_hits,
            ),
            channels=build_alert_channels(config, email_sender),
            claims=repos.settings,
            settings=settings,
            users=repos.user,
            config=config,
            clock=clock,
        ),
    )
