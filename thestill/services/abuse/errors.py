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

"""Spec #104 — the exceptions a policy raises when it says no.

Each carries a ``detail()`` — the body the web layer returns under
``detail`` (the shape ``HTTPException(detail=dict)`` produces, which the
frontend already parses) and the text MCP tools relay to the client.
"""

from typing import Any, Dict, Optional

# The 503 toast text per paused feature.
FEATURE_PAUSED_MESSAGES = {
    "new_feeds": "Adding new podcasts is paused right now.",
    "imports": "Imports are paused right now.",
    "translations": "Summary translations are paused right now.",
    "narration": "Briefing narration is paused right now.",
    "read_only": "Thestill is read-only right now. Please try again later.",
}


class AbusePreventionError(Exception):
    """Base: a spec #104 policy refused the action."""

    #: HTTP status the web layer maps this to.
    status_code = 400

    def detail(self) -> Dict[str, Any]:
        return {"error": "refused", "message": str(self)}

    def headers(self) -> Dict[str, str]:
        """Extra HTTP response headers (``Retry-After`` for a quota)."""
        return {}


class AccountDisabledError(AbusePreventionError):
    """The account is suspended or banned. The operator's reason is never
    part of the message: it is for admins only."""

    status_code = 403

    def __init__(self, status: str):
        self.status = status
        super().__init__("This account has been disabled.")

    def detail(self) -> Dict[str, Any]:
        return {"error": "account_disabled", "message": str(self)}


class FeaturePausedError(AbusePreventionError):
    """A runtime switch is off for ``feature`` (``new_feeds``, ``imports``,
    ``translations``, ``narration``)."""

    status_code = 503

    def __init__(self, feature: str, message: Optional[str] = None):
        self.feature = feature
        super().__init__(message or FEATURE_PAUSED_MESSAGES.get(feature, f"{feature} is paused right now."))

    def detail(self) -> Dict[str, Any]:
        return {"error": "feature_paused", "feature": self.feature, "message": str(self)}


class ReadOnlyError(FeaturePausedError):
    """Panic mode: ``read_only`` is on. Raised by the blunt method filter
    and by any feature check while it is on."""

    def __init__(self) -> None:
        super().__init__("read_only")

    def detail(self) -> Dict[str, Any]:
        return {"error": "read_only", "message": str(self)}


def _humanize_wait(seconds: int) -> str:
    if seconds >= 5400:
        hours = round(seconds / 3600)
        return f"{hours} hours"
    if seconds >= 3600:
        return "1 hour"
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


class QuotaExceededError(AbusePreventionError):
    """A per-user daily limit (spec #104 Phase 2). ``retry_after_seconds`` is
    None for a limit that no wait lifts (``follows_total``)."""

    status_code = 429

    def __init__(self, quota: str, limit: int, retry_after_seconds: Optional[int]):
        from .quotas import QUOTAS  # errors stays import-light; see the package docstring

        self.quota = quota
        self.limit = limit
        self.retry_after_seconds = retry_after_seconds
        wait = _humanize_wait(retry_after_seconds) if retry_after_seconds else ""
        super().__init__(QUOTAS[quota].message.format(limit=limit, wait=wait))

    def detail(self) -> Dict[str, Any]:
        return {
            "error": "quota_exceeded",
            "quota": self.quota,
            "limit": self.limit,
            "retry_after": self.retry_after_seconds,
            "message": str(self),
        }

    def headers(self) -> Dict[str, str]:
        return {"Retry-After": str(self.retry_after_seconds)} if self.retry_after_seconds else {}


class SignupRefusedError(AbusePreventionError):
    """The signup gate refused a new Google identity."""

    status_code = 403

    def __init__(self, mode: str):
        self.mode = mode
        super().__init__("Signups are paused right now.")

    def detail(self) -> Dict[str, Any]:
        return {"error": "signups_closed", "message": str(self)}


class AdminActionError(AbusePreventionError):
    """An admin action the rules forbid (``cannot_modify_self``,
    ``last_admin``, ``not_found``, ``single_user_mode``)."""

    status_code = 409

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def detail(self) -> Dict[str, Any]:
        return {"error": self.code, "message": str(self)}
