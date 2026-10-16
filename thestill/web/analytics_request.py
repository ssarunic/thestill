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

"""What a request contributes to an anonymous usage event (spec #95 §3.5).

The visitor key counts unique visitors per day and nothing more: an HMAC
of client IP and user agent keyed with the server secret and today's UTC
date. It changes at midnight, cannot be reversed, and the raw IP is never
stored or sent. Under Global Privacy Control there is no key at all.
"""

import hashlib
import hmac
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import Request
from structlog import get_logger

from .middleware import resolve_client_ip

logger = get_logger(__name__)


def request_gpc(request: Request) -> bool:
    """True when the browser sent Global Privacy Control (``Sec-GPC: 1``)."""
    return request.headers.get("sec-gpc", "").strip() == "1"


def daily_visitor_key(secret: str, client_ip: str, user_agent: str, day: date) -> str:
    """16 bytes (hex) of ``HMAC(secret ‖ day, ip ‖ ua)``."""
    key = f"{secret}|{day.isoformat()}".encode()
    return hmac.new(key, f"{client_ip}|{user_agent}".encode(), hashlib.sha256).hexdigest()[:32]


def login_flow_key(secret: str, oauth_state: str) -> str:
    """One sign-in attempt's anonymous id, derived from its OAuth ``state``.

    Both ends of the flow know the state (the login route mints it, the
    callback verifies it), so ``login_started`` and ``login_completed`` share
    this id and form a funnel without a new cookie, without the visitor key
    and without ever being merged into the account's person. The HMAC keeps
    the id from revealing the CSRF state it comes from.
    """
    return hmac.new(f"{secret}|login-flow".encode(), oauth_state.encode(), hashlib.sha256).hexdigest()[:32]


def visitor_key(request: Request, secret: str, config=None) -> Optional[str]:
    """Today's key for this visitor, or None under GPC."""
    if request_gpc(request):
        return None
    return daily_visitor_key(
        secret,
        resolve_client_ip(request, config) or "",
        request.headers.get("user-agent", ""),
        datetime.now(timezone.utc).date(),
    )


def capture_event(
    state,
    request: Request,
    event,
    *,
    user=None,
    anonymous_key: Optional[str] = None,
    consent_of=None,
) -> None:
    """Send one spec #95 usage event for this request, if events are on.

    Identified when ``user`` is given, else anonymous with ``anonymous_key``
    or today's visitor key (neither under GPC). ``consent_of`` applies a
    reader's opt-out to an event that is anonymous on purpose. Total: called
    from inside the OAuth callback, whose catch-all turns any exception into
    a failed sign-in.
    """
    sink = getattr(state, "analytics_sink", None)
    if sink is None or not sink.enabled:
        return
    try:
        if consent_of is not None and not consent_of.usage_analytics_enabled:
            return
        gpc = request_gpc(request)
        key = None
        if user is None and not gpc:
            key = anonymous_key or visitor_key(request, state.config.jwt_secret_key, state.config)
        sink.capture(event, user=user, visitor_key=key, gpc=gpc)
    except Exception as exc:  # noqa: BLE001 - analytics never breaks the request it describes
        logger.warning("analytics_capture_failed", analytics_event=getattr(event, "name", "?"), error=str(exc))
