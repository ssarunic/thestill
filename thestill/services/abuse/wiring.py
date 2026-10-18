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
from typing import TYPE_CHECKING

from .access_guard import AccessGuard
from .account_admin_service import AccountAdminService
from .audit_service import AuditLog
from .follow_policy import FollowPolicy, NewFeedPrecheck
from .settings_service import SettingsService
from .signup_policy import SignupPolicy

if TYPE_CHECKING:
    from ...repositories.factory import RepositoryBundle
    from ...utils.config import Config


@dataclass
class AbuseServices:
    settings: SettingsService
    audit: AuditLog
    guard: AccessGuard
    signup: SignupPolicy
    follow_policy: FollowPolicy
    new_feed_precheck: NewFeedPrecheck
    account_admin: AccountAdminService


def build_abuse_services(config: "Config", repos: "RepositoryBundle") -> AbuseServices:
    audit = AuditLog(repos.audit_log)
    settings = SettingsService(repos.settings, config, audit)
    return AbuseServices(
        settings=settings,
        audit=audit,
        guard=AccessGuard(repos.user, settings, multi_user=config.multi_user),
        signup=SignupPolicy(settings),
        follow_policy=FollowPolicy(settings),
        new_feed_precheck=NewFeedPrecheck(settings, repos.follower),
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
        ),
    )
