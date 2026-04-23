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
Log-safety helpers: redaction of sensitive keys (spec #25 item 3.5) and
neutering of control characters that could forge JSON-log lines
(spec #25 item 3.9).

Exposes a single structlog processor, :func:`log_safety_processor`, that
walks the event dict and rewrites sensitive values in place. Wire it
into ``thestill.logging`` so every caller inherits the behaviour — the
previous approach of chasing every ``logger.*`` call site was a losing
game.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, MutableMapping

# Keys that almost certainly carry secrets. Match case-insensitively on
# substring so e.g. ``auth_token``, ``X-Api-Key``, ``oauth_state`` all
# land in the redact bucket.
_REDACT_KEY_SUBSTRINGS: tuple = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "api-key",
    "apikey",
    "client_secret",
    "code",          # OAuth authorization code — short-lived but powerful
    "state",         # OAuth CSRF token
    "session",
)

# Replacement token. Deliberately visually distinct so "it was redacted"
# is obvious when skimming logs.
_REDACTED = "[redacted]"

# Control characters that can forge log lines (CR, LF, NUL, ESC, DEL,
# plus C0 / C1 controls). Tab is preserved — operators occasionally
# include it intentionally, and it's harmless in both console and JSON.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(needle in k for needle in _REDACT_KEY_SUBSTRINGS)


def sanitize_control_chars(value: Any) -> Any:
    """
    Replace control characters in strings with their escaped form so a
    hostile RSS title like ``\\r\\n{"level":"critical"}`` can't forge a
    log line in downstream JSON consumers.
    """
    if isinstance(value, str):
        return _CONTROL_RE.sub(
            lambda m: "\\x{:02x}".format(ord(m.group(0))), value
        )
    return value


def redact_mapping(mapping: Mapping[str, Any]) -> dict:
    """
    Return a shallow copy of ``mapping`` with sensitive-key values
    replaced by ``[redacted]``. Nested dicts are walked recursively so
    ``webhook_metadata={"token": "..."}`` is also cleaned.
    """
    out: dict = {}
    for key, value in mapping.items():
        if not isinstance(key, str):
            out[key] = value
            continue
        if _is_sensitive_key(key):
            out[key] = _REDACTED
        elif isinstance(value, Mapping):
            out[key] = redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            out[key] = type(value)(
                redact_mapping(v) if isinstance(v, Mapping) else sanitize_control_chars(v) for v in value
            )
        else:
            out[key] = sanitize_control_chars(value)
    return out


def log_safety_processor(logger, method_name, event_dict: MutableMapping[str, Any]):
    """
    structlog processor that applies :func:`redact_mapping` + control-char
    sanitisation to every event emitted from anywhere in the app.

    Attach at the END of the structlog processor chain, after
    ``add_log_level`` / ``TimeStamper`` so those synthetic keys don't
    themselves get scrubbed.
    """
    cleaned = redact_mapping(event_dict)
    event_dict.clear()
    event_dict.update(cleaned)
    return event_dict


__all__ = [
    "redact_mapping",
    "sanitize_control_chars",
    "log_safety_processor",
]
