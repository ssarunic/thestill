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
JWT utilities for token encoding and decoding.

Uses PyJWT for secure token handling with proper error management.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from structlog import get_logger

from ..models.user import TokenPayload

logger = get_logger(__name__)


def create_access_token(
    user_id: str,
    secret_key: str,
    algorithm: str = "HS256",
    expires_days: int = 30,
) -> str:
    """Create a signed JWT access token.

    Spec #25 item 4.2: every issued token now carries a ``jti`` (random
    UUID4). Combined with the server-side revocation deny-list this is
    what makes logout actually invalidate a token — without ``jti`` we'd
    have nothing stable to deny by.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=expires_days)

    payload = {
        "sub": user_id,
        "iat": now,
        "exp": expire,
        "jti": str(uuid.uuid4()),
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
            # ``jti`` was added by spec #25 item 4.2. Tokens minted before
            # that change won't have it; treat the absent case as empty
            # string (auth_service's revocation check just won't match).
            jti=payload.get("jti", ""),
        )
    except jwt.ExpiredSignatureError:
        logger.debug("Token has expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"Invalid token: {e}")
        return None


def _unsafe_peek_token_expiry(token: str) -> Optional[datetime]:
    """
    Extract ``exp`` from a token without verifying the signature.

    This is labelled ``_unsafe_`` because an attacker can forge arbitrary
    claims — the returned datetime must NEVER be used for an auth
    decision. Call sites are limited to:

    * observability logging (``token_expires_at=...`` fields);
    * deciding whether to ATTEMPT a refresh — the refresh itself must
      re-verify the token server-side.

    Anything that needs trustworthy expiry must call
    :func:`get_token_expiry` with the secret key.

    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_expiry(token: str, secret_key: str, algorithm: str = "HS256") -> Optional[datetime]:
    """
    Extract expiry from a token **after verifying its signature**.

    Unlike :func:`_unsafe_peek_token_expiry`, this is safe to use for
    auth decisions.  Returns ``None`` for any token that fails
    validation, including expired tokens.
    """
    payload = decode_token(token, secret_key, algorithm=algorithm)
    return payload.exp if payload else None


def is_token_expiring_soon(
    token: str,
    secret_key: str,
    *,
    algorithm: str = "HS256",
    threshold_days: int = 7,
) -> bool:
    """
    Check if a signed, valid token is within ``threshold_days`` of
    its expiry. Used to drive silent refresh.

    previously this function trusted unverified
    ``exp`` claims, which let an attacker pretend their forged token
    was fresh. Now we always verify the signature; an invalid or
    already-expired token returns ``True`` so the caller refreshes
    (or re-authenticates).
    """
    expiry = get_token_expiry(token, secret_key, algorithm=algorithm)
    if not expiry:
        return True  # Invalid, expired, or missing exp — refresh path.

    threshold = datetime.now(timezone.utc) + timedelta(days=threshold_days)
    return expiry <= threshold
