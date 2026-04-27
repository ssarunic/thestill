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

"""
Authentication service with support for single-user and multi-user modes.

Single-user mode (MULTI_USER=false): Auto-creates a default user, no login required.
Multi-user mode (MULTI_USER=true): Requires Google OAuth authentication.
"""

import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import urlencode

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from structlog import get_logger

from ..models.user import TokenPayload, User
from ..repositories.user_repository import UserRepository
from ..utils.config import Config
from ..utils.geoip import lookup_country_from_ip
from ..utils.jwt import create_access_token, decode_token

logger = get_logger(__name__)

# Default user constants for single-user mode
DEFAULT_USER_EMAIL = "local@thestill.me"
DEFAULT_USER_NAME = "Local User"

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class AuthService:
    """
    Authentication service supporting both single-user and multi-user modes.

    In single-user mode, automatically creates and returns a default user.
    In multi-user mode, handles Google OAuth flow and JWT token management.
    """

    def __init__(self, config: Config, user_repository: UserRepository):
        """
        Initialize the auth service.

        Args:
            config: Application configuration
            user_repository: Repository for user persistence
        """
        self.config = config
        self.user_repository = user_repository
        self.multi_user = config.multi_user

        # JWT settings
        self.jwt_secret_key = config.jwt_secret_key
        self.jwt_algorithm = config.jwt_algorithm
        self.jwt_expire_days = config.jwt_expire_days

        # Google OAuth settings (only needed for multi-user mode)
        self.google_client_id = config.google_client_id
        self.google_client_secret = config.google_client_secret

        # Cache for default user in single-user mode
        self._default_user: Optional[User] = None

        # Validate configuration
        self._validate_config()

        logger.info(f"AuthService initialized in {'multi-user' if self.multi_user else 'single-user'} mode")

    def _validate_config(self):
        """Validate authentication configuration."""
        if self.multi_user:
            if not self.google_client_id or not self.google_client_secret:
                raise ValueError(
                    "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required for multi-user mode. "
                    "Set MULTI_USER=false for single-user mode or configure Google OAuth credentials."
                )

        if not self.jwt_secret_key:
            if self.multi_user:
                raise ValueError(
                    "JWT_SECRET_KEY is required for multi-user mode. Generate one with: openssl rand -hex 32"
                )
            else:
                # For single-user mode, generate a random secret if not provided
                logger.warning("JWT_SECRET_KEY not set, generating random key for this session")
                self.jwt_secret_key = secrets.token_hex(32)

    def get_or_create_default_user(self) -> User:
        """
        Get or create the default user for single-user mode.

        Returns:
            The default local user
        """
        if self._default_user:
            return self._default_user

        # Check if default user exists
        user = self.user_repository.get_by_email(DEFAULT_USER_EMAIL)

        if not user:
            # Create default user
            user = User(
                id=str(uuid.uuid4()),
                email=DEFAULT_USER_EMAIL,
                name=DEFAULT_USER_NAME,
                created_at=datetime.now(timezone.utc),
            )
            user = self.user_repository.save(user)
            logger.info(f"Created default user: {user.email}")
        else:
            logger.debug(f"Using existing default user: {user.email}")

        self._default_user = user
        return user

    def get_google_auth_url(self, redirect_uri: str, state: Optional[str] = None) -> Tuple[str, str]:
        """
        Generate Google OAuth authorization URL.

        Args:
            redirect_uri: URL to redirect to after authorization
            state: Optional state parameter for CSRF protection (generated if not provided)

        Returns:
            Tuple of (authorization_url, state)
        """
        if not self.multi_user:
            raise RuntimeError("Google OAuth is not available in single-user mode")

        # Generate state if not provided
        if not state:
            state = secrets.token_urlsafe(32)

        params = {
            "client_id": self.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }

        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        return auth_url, state

    async def handle_google_callback(
        self,
        code: str,
        redirect_uri: str,
    ) -> Tuple[User, str]:
        """
        Handle Google OAuth callback.

        Exchanges the authorization code for tokens, fetches user info,
        creates or updates the user, and returns a JWT.

        Args:
            code: Authorization code from Google
            redirect_uri: The redirect URI used in the authorization request

        Returns:
            Tuple of (User, JWT token)

        Raises:
            RuntimeError: If Google OAuth is not available in single-user mode
            httpx.HTTPError: If OAuth token exchange fails
        """
        if not self.multi_user:
            raise RuntimeError("Google OAuth is not available in single-user mode")

        async with AsyncOAuth2Client(
            client_id=self.google_client_id,
            client_secret=self.google_client_secret,
        ) as client:
            # Exchange code for tokens
            token = await client.fetch_token(
                GOOGLE_TOKEN_URL,
                code=code,
                redirect_uri=redirect_uri,
            )

            # Fetch user info
            client.token = token
            resp = await client.get(GOOGLE_USERINFO_URL)
            resp.raise_for_status()
            userinfo = resp.json()

        # Extract user data from Google response
        google_id = userinfo.get("sub")
        email = userinfo.get("email")
        name = userinfo.get("name")
        picture = userinfo.get("picture")

        if not email:
            raise ValueError("Google did not return an email address")

        # Find or create user
        user = self.user_repository.get_by_google_id(google_id)

        if user:
            # Update existing user
            user.name = name
            user.picture = picture
            user.last_login_at = datetime.now(timezone.utc)
            user = self.user_repository.save(user)
            logger.info(f"User logged in: {user.email}")
        else:
            # Check if user exists by email (account linking)
            user = self.user_repository.get_by_email(email)

            if user:
                # Link Google account to existing user
                user.google_id = google_id
                user.name = name
                user.picture = picture
                user.last_login_at = datetime.now(timezone.utc)
                user = self.user_repository.save(user)
                logger.info(f"Linked Google account to existing user: {user.email}")
            else:
                # Create new user
                user = User(
                    id=str(uuid.uuid4()),
                    email=email,
                    name=name,
                    picture=picture,
                    google_id=google_id,
                    created_at=datetime.now(timezone.utc),
                    last_login_at=datetime.now(timezone.utc),
                )
                self.user_repository.save(user)
                # Re-fetch to get correct ID (UPSERT may have updated existing user)
                user = self.user_repository.get_by_email(email)
                logger.info(f"Created new user: {user.email}")

        # Create JWT token
        jwt_token = self.create_jwt(user)

        return user, jwt_token

    def create_jwt(self, user: User) -> str:
        """
        Create a JWT token for the user.

        Args:
            user: The user to create a token for

        Returns:
            Signed JWT token string
        """
        return create_access_token(
            user_id=user.id,
            secret_key=self.jwt_secret_key,
            algorithm=self.jwt_algorithm,
            expires_days=self.jwt_expire_days,
        )

    def verify_jwt(self, token: str) -> Optional[TokenPayload]:
        """
        Verify and decode a JWT token.

        Args:
            token: The JWT token to verify

        Returns:
            TokenPayload if valid, None if invalid or expired
        """
        return decode_token(
            token=token,
            secret_key=self.jwt_secret_key,
            algorithm=self.jwt_algorithm,
        )

    def get_user_from_token(self, token: str) -> Optional[User]:
        """
        Get the full user object from a JWT token.

        Args:
            token: The JWT token

        Returns:
            User if token is valid and user exists, None otherwise
        """
        payload = self.verify_jwt(token)
        if not payload:
            return None

        return self.user_repository.get_by_id(payload.sub)

    async def maybe_infer_region(self, user: User, client_ip: Optional[str]) -> Optional[str]:
        """Infer and persist the user's region from their IP, if appropriate.

        No-ops when the user has already locked a region, when ``client_ip``
        is missing/private/unroutable, or when the lookup fails. On success
        the user row is updated and the resolved code is returned. The
        ``region_locked`` flag stays False so that an explicit user choice
        later can still freely override (and lock).
        """
        if user.region_locked:
            return user.region
        if user.region:
            # Already inferred previously; don't keep re-querying ipinfo on
            # every login while the user still hasn't picked one explicitly.
            return user.region

        country = await lookup_country_from_ip(client_ip)
        if not country:
            return None

        self.user_repository.update_region(user.id, country, locked=False)
        user.region = country
        # Refresh single-user-mode cache so subsequent calls see it.
        if self._default_user and self._default_user.id == user.id:
            self._default_user.region = country
        logger.info("user_region_inferred", user_id=user.id, region=country)
        return country

    def set_user_region(self, user: User, region: Optional[str]) -> User:
        """Persist an explicit user-chosen region and lock further inference.

        Passing ``None`` clears the region but still locks; callers that
        want to fall back to inference should re-introduce that path
        deliberately rather than relying on a clear-and-unlock here.
        """
        normalised: Optional[str] = None
        if region is not None:
            cleaned = region.strip().lower()
            if len(cleaned) != 2 or not cleaned.isalpha():
                raise ValueError("region must be a 2-letter ISO 3166-1 alpha-2 code")
            normalised = cleaned

        self.user_repository.update_region(user.id, normalised, locked=True)
        user.region = normalised
        user.region_locked = True
        if self._default_user and self._default_user.id == user.id:
            self._default_user.region = normalised
            self._default_user.region_locked = True
        logger.info("user_region_set", user_id=user.id, region=normalised)
        return user

    def get_current_user(self, token: Optional[str] = None) -> Optional[User]:
        """
        Get the current user based on mode and token.

        In single-user mode, always returns the default user.
        In multi-user mode, returns the user from the token or None.

        Args:
            token: JWT token (only used in multi-user mode)

        Returns:
            User if authenticated, None if not authenticated in multi-user mode
        """
        if not self.multi_user:
            return self.get_or_create_default_user()

        if not token:
            return None

        return self.get_user_from_token(token)
