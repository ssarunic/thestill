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

"""
JWT utilities for token encoding and decoding.

Uses PyJWT for secure token handling with proper error management.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from ..models.user import TokenPayload

logger = logging.getLogger(__name__)


def create_access_token(
    user_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expires_days: int = 30,
) -> str:
    """
    Create a signed JWT access token.

    Args:
        user_id: The user's unique identifier (UUID)
        secret_key: Secret key for signing the token
        algorithm: JWT signing algorithm (default: HS256)
        expires_days: Token expiration in days (default: 30)

    Returns:
        Encoded JWT token string
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=expires_days)

    payload = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
    }

    token = jwt.encode(payload, secret_key, algorithm=algorithm)
    logger.debug(f"Created access token for user {user_id}, expires {expire.isoformat()}")
    return token


def decode_token(
    token: str,
    secret_key: str,
    algorithm: str = "HS256",
) -> Optional[TokenPayload]:
    """
    Decode and validate a JWT token.

    Args:
        token: The JWT token string to decode
        secret_key: Secret key used for token verification
        algorithm: JWT signing algorithm (default: HS256)

    Returns:
        TokenPayload if valid, None if invalid or expired
    """
    try:
        payload = jwt.decode(token, secret_key, algorithms=[algorithm])

        return TokenPayload(
            sub=payload["sub"],
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            iat=datetime.fromtimestamp(payload["iat"], tz=timezone.utc),
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid token: {e}")
        return None


def get_token_expiry(token: str) -> Optional[datetime]:
    """
    Extract expiry time from a token without full validation.

    This is useful for checking if a token is close to expiry
    without needing the secret key.

    Args:
        token: The JWT token string

    Returns:
        Expiry datetime if extractable, None otherwise
    """
    try:
        # Decode without verification to get expiry
        payload = jwt.decode(token, options={"verify_signature": False})
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        return None
    except jwt.InvalidTokenError:
        return None


def is_token_expiring_soon(token: str, threshold_days: int = 7) -> bool:
    """
    Check if a token is expiring within the threshold period.

    Useful for implementing silent token refresh.

    Args:
        token: The JWT token string
        threshold_days: Number of days before expiry to consider "soon"

    Returns:
        True if token expires within threshold, False otherwise
    """
    expiry = get_token_expiry(token)
    if not expiry:
        return True  # If we can't determine expiry, assume it's expiring

    threshold = datetime.now(timezone.utc) + timedelta(days=threshold_days)
    return expiry <= threshold
