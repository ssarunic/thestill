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
Signed one-click unsubscribe tokens (spec #51).

``itsdangerous``-style HMAC signing over the app secret: the token embeds
the user id and a purpose tag, signed with HMAC-SHA256. Deliberately NOT a
JWT — the auth layer's JWTs are login credentials signed with the same
secret, and an unsubscribe link sits unauthenticated in an email body. A
distinct format guarantees the two can never be confused for one another
in either direction.

No expiry: a dead unsubscribe link is a CAN-SPAM violation, and the token
grants exactly one narrow capability (flip ``email_enabled`` off), so a
long-lived token is the correct trade-off.
"""

import base64
import hashlib
import hmac
import json
from typing import Optional

# Versioned purpose tag: scopes the signature so this token can never be
# replayed against any other signed-token surface added later.
_PURPOSE = "briefing-email-unsubscribe.v1"


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _signature(payload: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), _PURPOSE.encode("ascii") + b"." + payload, hashlib.sha256).digest()


def make_unsubscribe_token(user_id: str, secret: str) -> str:
    """Return a signed token identifying ``user_id`` for unsubscribe."""
    if not secret:
        raise ValueError("A non-empty secret is required to sign unsubscribe tokens")
    payload = json.dumps({"u": user_id}, separators=(",", ":")).encode("utf-8")
    return f"{_b64encode(payload)}.{_b64encode(_signature(payload, secret))}"


def verify_unsubscribe_token(token: str, secret: str) -> Optional[str]:
    """Return the embedded user id, or ``None`` for any invalid token.

    All failure modes (malformed, tampered, wrong secret) collapse to
    ``None`` — the route maps that to one generic error page, leaking
    nothing about which check failed.
    """
    if not secret or not token or token.count(".") != 1:
        return None
    payload_b64, signature_b64 = token.split(".", 1)
    try:
        payload = _b64decode(payload_b64)
        signature = _b64decode(signature_b64)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(signature, _signature(payload, secret)):
        return None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None
    user_id = data.get("u") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, str) and user_id else None
