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

"""Loopback addresses and the OAuth redirect URI policy (spec #109).

One place for "is this a loopback address", which the MCP OAuth server,
the personal-URL mint route and config validation all ask, and for which
redirect URIs a client may register at all.
"""

import re
from urllib.parse import urlsplit

#: Hostnames as ``urlsplit(...).hostname`` returns them (IPv6 unbracketed).
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Schemes that execute or read locally when a browser navigates to them.
_FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "vbscript", "file", "blob", "about", "filesystem"})
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*$")


def is_loopback_host(host: str) -> bool:
    return host.lower() in LOOPBACK_HOSTS


def is_loopback_uri(uri: str) -> bool:
    """An ``http`` URI on a loopback host (RFC 8252 §7.3)."""
    try:
        parts = urlsplit(uri)
        return parts.scheme == "http" and is_loopback_host(parts.hostname or "")
    except ValueError:
        return False


def is_allowed_redirect_uri(uri: str) -> bool:
    """Whether a client may register (and be sent to) ``uri``.

    ``https`` with a host; ``http`` on loopback only (native apps such as
    Claude Code); or a native app's private-use scheme (RFC 8252 §7.1).
    Never a scheme a browser would execute or read locally, never a
    fragment (RFC 6749 §3.1.2), never credentials in the URI.
    """
    try:
        parts = urlsplit(uri)
        hostname = parts.hostname
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not _SCHEME.match(scheme) or scheme in _FORBIDDEN_SCHEMES:
        return False
    if parts.fragment or parts.username or parts.password:
        return False
    if scheme == "https":
        return bool(hostname)
    if scheme == "http":
        return is_loopback_host(hostname or "")
    if scheme in ("ws", "wss", "ftp", "mailto", "tel"):
        return False
    # A private-use scheme for a native app, e.g. ``com.example.app:/cb``.
    return True
