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

"""Spec #104 §1.3 — the runtime settings, their env defaults and validation.

Every key the operator can change at runtime is declared here once. The
web API, the CLI (``thestill settings set``) and ``SettingsService`` all
validate through the same ``SettingSpec``.
"""

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List

from .quotas import QUOTA_NAMES

if TYPE_CHECKING:
    from ...utils.config import Config

SIGNUP_MODES = ("open", "allowlist", "closed")


#: Costly paths a switch can pause. ``read_only`` pauses all of them.
FEATURES = ("new_feeds", "imports", "translations", "narration")

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _validate_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("expected true or false")


def _parse_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"expected true or false, got {raw!r}")


def _validate_signup_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in SIGNUP_MODES:
        return value.strip().lower()
    raise ValueError(f"expected one of {', '.join(SIGNUP_MODES)}")


def normalize_allowlist_entry(entry: str) -> str:
    """``Alice@Example.com`` → ``alice@example.com``; ``@Example.com`` →
    ``@example.com``. Raises on anything that is neither."""
    cleaned = entry.strip().lower()
    local, at, domain = cleaned.partition("@")
    if not at or not domain or "@" in domain or "." not in domain or " " in cleaned:
        raise ValueError(f"not an email address or @domain: {entry!r}")
    return cleaned


def _validate_allowlist(value: Any) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a list of email addresses or @domains")
    normalised: List[str] = []
    for entry in value:
        if entry.strip():
            item = normalize_allowlist_entry(entry)
            if item not in normalised:
                normalised.append(item)
    return normalised


def _parse_allowlist(raw: str) -> List[str]:
    raw = raw.strip()
    if raw.startswith("["):
        return json.loads(raw)
    return [part for part in raw.replace("\n", ",").split(",") if part.strip()]


_ENTITY_ID_RE = re.compile(r"^(person|company|product|topic):[a-z0-9][a-z0-9-]{0,110}$")


def _validate_entity_ids(value: Any) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("expected a list of entity ids (person:<slug>, company:<slug>, ...)")
    normalised: List[str] = []
    for entry in value:
        entry = entry.strip().lower()
        if not entry:
            continue
        if not _ENTITY_ID_RE.match(entry):
            raise ValueError(f"not an entity id: {entry!r}")
        if entry not in normalised:
            normalised.append(entry)
    return normalised


def _validate_quota_overrides(value: Any) -> Dict[str, Dict[str, int]]:
    """``{user_id: {quota: limit}}``; 0 means unlimited. Empty maps drop out."""
    if not isinstance(value, dict):
        raise ValueError("expected {user_id: {quota: limit}}")
    normalised: Dict[str, Dict[str, int]] = {}
    for user_id, limits in value.items():
        if not isinstance(user_id, str) or not isinstance(limits, dict):
            raise ValueError("expected {user_id: {quota: limit}}")
        for quota, limit in limits.items():
            if quota not in QUOTA_NAMES:
                raise ValueError(f"unknown quota {quota!r}; known: {', '.join(QUOTA_NAMES)}")
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError(f"limit for {quota} must be a whole number >= 0")
        if limits:
            normalised[user_id] = dict(limits)
    return normalised


#: Thresholds of the Phase 3 alerts; ``alerts`` overrides any subset of the
#: env defaults (``Config.alert_<name>``).
ALERT_THRESHOLDS = (
    "quota_pressure",
    "rate_limit_pressure",
    "signup_spike",
    "feed_spike",
    "queue_backlog",
    "spend_breaker_minutes",
)
SPEND_BREAKER_ACTIONS = ("none", "pause_new_feeds")


def _validate_alerts(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected {threshold: number, spend_breaker_action: ...}")
    for key, item in value.items():
        if key == "spend_breaker_action":
            if item not in SPEND_BREAKER_ACTIONS:
                raise ValueError(f"spend_breaker_action must be one of {', '.join(SPEND_BREAKER_ACTIONS)}")
        elif key in ALERT_THRESHOLDS:
            if isinstance(item, bool) or not isinstance(item, int) or item < 0:
                raise ValueError(f"{key} must be a whole number >= 0")
        else:
            raise ValueError(
                f"unknown alert setting {key!r}; known: {', '.join(ALERT_THRESHOLDS)}, spend_breaker_action"
            )
    return dict(value)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    description: str
    env_default: Callable[["Config"], Any]
    #: Normalises a JSON value or raises ``ValueError``.
    validate: Callable[[Any], Any]
    #: Turns CLI text into a JSON value (then ``validate`` runs).
    parse: Callable[[str], Any]
    #: Used only if the env default itself is invalid (``load_config``
    #: refuses to start on one, so this guards hand-built configs). Fails
    #: closed: no new signups.
    failsafe: Any = None


def _switch(key: str, description: str, default: Callable[["Config"], bool] = lambda _c: True) -> SettingSpec:
    return SettingSpec(key, description, default, _validate_bool, _parse_bool, failsafe=False)


SETTINGS: Dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            "signup_mode",
            "Who may create an account: open, allowlist or closed. Existing users always sign in.",
            lambda c: _validate_signup_mode(c.signup_mode or "open"),
            _validate_signup_mode,
            lambda raw: raw.strip(),
            failsafe="closed",
        ),
        SettingSpec(
            "signup_allowlist",
            "Emails or @domains allowed to sign up when signup_mode is allowlist.",
            lambda c: _validate_allowlist(list(c.signup_allowlist)),
            _validate_allowlist,
            _parse_allowlist,
            failsafe=[],
        ),
        _switch("new_feeds_enabled", "Off: refuse any action that takes a feed from zero followers to one."),
        _switch("imports_enabled", "Off: refuse new imports."),
        _switch("translations_enabled", "Off: serve existing summary translations only."),
        _switch(
            "narration_enabled",
            "Off: manual and scheduled briefing narration stop.",
            lambda c: bool(c.narration_enabled),
        ),
        _switch(
            "read_only",
            "Panic mode: non-admin writes return 503 and every feature above is paused.",
            lambda _c: False,
        ),
        _switch(
            "public_prerender_enabled",
            "Off: public pages send the head-only shell, with no server-written content (spec #105).",
            lambda c: bool(c.public_prerender_enabled),
        ),
        SettingSpec(
            "entity_noindex",
            "Entity pages to keep out of search (noindex, not in the sitemap) whatever the spec #105 rule says: "
            '["person:altruist", ...]. Pages stay reachable.',
            lambda _c: [],
            _validate_entity_ids,
            _parse_allowlist,
            failsafe=[],
        ),
        SettingSpec(
            "quota_overrides",
            "Per-user quota limits that replace the env defaults: {user_id: {quota: limit}}. Edit with "
            "`thestill user quota`.",
            lambda _c: {},
            _validate_quota_overrides,
            json.loads,
            failsafe={},
        ),
        SettingSpec(
            "alerts",
            "Alert thresholds and the spend breaker's action, overriding the ALERT_* env defaults: "
            '{"queue_backlog": 1000, "spend_breaker_action": "pause_new_feeds"}.',
            lambda _c: {},
            _validate_alerts,
            json.loads,
            failsafe={},
        ),
    )
}


def get_spec(key: str) -> SettingSpec:
    try:
        return SETTINGS[key]
    except KeyError:
        raise KeyError(f"unknown setting {key!r}; known: {', '.join(SETTINGS)}") from None
