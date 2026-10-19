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

"""User authentication models and follower relationships for single-user and multi-user modes."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# The single-user mode account. Lives with the model (not the auth service)
# so repositories can refer to it without importing a service.
DEFAULT_USER_EMAIL = "local@thestill.me"


class UserStatus(str, Enum):
    """Spec #104 — account status. Anything but ``active`` blocks sign-in
    and every request in multi-user mode."""

    ACTIVE = "active"
    # Blocked, follows kept: lifting it restores the account exactly.
    SUSPENDED = "suspended"
    # Blocked, and the spending it caused stops (follows moved aside).
    BANNED = "banned"


# Fields the API never returns to the account's own owner: operator notes
# and revocation bookkeeping (spec #104), plus the provider id.
PRIVATE_USER_FIELDS = frozenset(
    {"google_id", "status_reason", "status_changed_at", "status_changed_by", "tokens_valid_after"}
)


class User(BaseModel):
    """
    User model for authentication.

    In single-user mode (MULTI_USER=false), a default user is auto-created.
    In multi-user mode (MULTI_USER=true), users are created via Google OAuth.

    Attributes:
        id: Internal UUID for the user
        email: User's email address (unique)
        name: Display name from Google profile (optional)
        picture: Profile photo URL from Google (optional)
        google_id: Google's unique user ID (null for default single-user)
        created_at: When the user account was created
        last_login_at: When the user last logged in
        region: ISO 3166-1 alpha-2 country code (lowercase). Used to filter
            region-scoped content like top podcasts. None until inferred from
            IP or explicitly set by the user.
        region_locked: True once the user has explicitly picked a region. While
            False, the server may overwrite ``region`` from an IP-based guess
            on each login; once True, automatic inference is suppressed.
        is_admin: True if the user may view and control the processing pipeline
            (task queue + dead-letter queue). In single-user mode the default
            user is always an admin; in multi-user mode this is off by default
            and flipped manually in the database.
        signup_source: Spec #95 — the kind of public page the account was
            created from (``landing``, ``top``, ``podcast``, ``episode``,
            ``entity``, ``share``) or ``direct``. Set once when the row is
            created and never updated; None for accounts created before it
            was tracked.
        usage_analytics_enabled: Spec #95 — the Settings switch for usage
            events (PostHog). Off means the browser loads no analytics code
            and the server drops every event for this user. Product tables
            still count them (Layer A).
        status: Spec #104 — ``active``, ``suspended`` or ``banned``. Set only
            through the account admin service, never by ``save()``.
        status_reason: The operator's note; shown only to admins.
        status_changed_at / status_changed_by: When and by which admin
            (None for the CLI).
        tokens_valid_after: JWTs issued before this instant are rejected
            ("sign out everywhere"; also set on suspend and ban).
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None
    google_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = None
    region: Optional[str] = None
    region_locked: bool = False
    is_admin: bool = False
    signup_source: Optional[str] = None
    usage_analytics_enabled: bool = True
    status: UserStatus = UserStatus.ACTIVE
    status_reason: Optional[str] = None
    status_changed_at: Optional[datetime] = None
    status_changed_by: Optional[str] = None
    tokens_valid_after: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE


class TokenPayload(BaseModel):
    """JWT token payload claims.

    Attributes:
        sub: Subject — the user ID.
        exp: Expiration time.
        iat: Issued at time.
        jti: JWT ID (random UUID per token). Used by the server-side
            revocation deny-list (spec #25 item 4.2) so logout can
            actually invalidate. May be empty string for legacy tokens
            minted before that change.
    """

    sub: str  # user_id
    exp: datetime
    iat: datetime
    jti: str = ""


class PodcastFollower(BaseModel):
    """
    User-Podcast following relationship.

    Represents a user following (subscribing to) a podcast.
    Podcasts are shared resources - processing happens once, delivered to many.

    Attributes:
        id: Internal UUID for the relationship
        user_id: ID of the user following the podcast
        podcast_id: ID of the podcast being followed
        created_at: When the user started following
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    podcast_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
