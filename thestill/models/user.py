# Copyright 2025 thestill.me
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

"""User authentication models for single-user and multi-user modes."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


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
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None
    google_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = None


class TokenPayload(BaseModel):
    """
    JWT token payload claims.

    Attributes:
        sub: Subject - the user ID
        exp: Expiration time
        iat: Issued at time
    """

    sub: str  # user_id
    exp: datetime
    iat: datetime
