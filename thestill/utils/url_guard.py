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
SSRF guard for user-supplied URLs (spec #25, item 1.2).

Every outbound HTTP fetch triggered by user input (RSS feed URL, episode
audio URL, Apple-podcast lookup, external transcript URL, etc.) must
pass through :func:`validate_public_url` before the request is issued,
and each redirect hop must be re-validated via :func:`guarded_session`.

The guard rejects:

* non-http(s) schemes (``file://``, ``gopher://``, ``ftp://``, ``data:`` …)
* hostnames that resolve to private, loopback, link-local, multicast,
  or otherwise reserved IP ranges — including the cloud-metadata endpoint
  ``169.254.169.254`` (AWS / GCP IAM).

Both IPv4 and IPv6 are covered.  Every A/AAAA record is checked; if
*any* resolved address is internal the URL is rejected.  DNS rebinding
is mitigated in two ways: (a) callers that need to read the body must
use the session returned by :func:`guarded_session`, which re-validates
on redirect, and (b) the TOCTOU window between DNS lookup and TCP
connect remains — documented rather than solved here; a full defence
would need a custom HTTPAdapter that pins the socket to the resolved
address.  Pinning is left for a follow-up.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from structlog import get_logger
from urllib3.util.retry import Retry

logger = get_logger(__name__)


_ALLOWED_SCHEMES: Tuple[str, ...] = ("http", "https")
_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT_SECONDS = 30


class UnsafeURLError(ValueError):
    """Raised when a user-supplied URL targets a forbidden destination."""


@dataclass(frozen=True)
class ResolvedHost:
    """Outcome of a hostname lookup."""

    hostname: str
    addresses: Tuple[str, ...]


def _env_allowlist() -> Iterable[str]:
    """Hostnames exempted from the guard (e.g. a Dalston on ``localhost``)."""
    raw = os.getenv("URL_GUARD_ALLOWLIST", "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _is_allowlisted_host(hostname: str) -> bool:
    return hostname.lower() in _env_allowlist()


def _resolve(hostname: str) -> Tuple[str, ...]:
    """Resolve *hostname* to every A/AAAA it advertises."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS lookup failed for {hostname!r}: {exc}") from exc
    return tuple({info[4][0] for info in infos})


def _ip_is_public(address: str) -> bool:
    """Return ``True`` only if *address* is a globally routable public IP."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # ``is_global`` covers the "public internet" case and already excludes
    # private, loopback, link-local, multicast, reserved, and unspecified
    # ranges. We spell a few extras out explicitly so future ipaddress
    # changes don't silently relax the check.
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return bool(ip.is_global)


def validate_public_url(url: str) -> ResolvedHost:
    """
    Assert that *url* is safe to fetch from a user-controlled input path.

    Raises:
        UnsafeURLError: scheme not http(s), hostname missing, DNS failure,
            or any resolved address is not publicly routable.

    Returns:
        The resolved hostname + addresses, useful if the caller wants to
        pin the connection to the exact IP that was validated.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"disallowed scheme {scheme!r} in {url!r}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError(f"no hostname in {url!r}")

    # Bare-IP URLs: validate the literal without DNS.
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_is_public(hostname):
            raise UnsafeURLError(f"URL targets non-public address {hostname!r}")
        return ResolvedHost(hostname=hostname, addresses=(hostname,))

    if _is_allowlisted_host(hostname):
        # Caller has opted this host in via URL_GUARD_ALLOWLIST (e.g. a
        # self-hosted Dalston on the same machine). Still resolve so we
        # know what we hit, but skip the public-IP check.
        addresses = _resolve(hostname)
        logger.info("url_guard.allowlisted", hostname=hostname, addresses=addresses)
        return ResolvedHost(hostname=hostname, addresses=addresses)

    addresses = _resolve(hostname)
    if not addresses:
        raise UnsafeURLError(f"hostname {hostname!r} resolved to no addresses")

    bad = [addr for addr in addresses if not _ip_is_public(addr)]
    if bad:
        raise UnsafeURLError(
            f"hostname {hostname!r} resolves to non-public address(es): {bad}"
        )
    return ResolvedHost(hostname=hostname, addresses=addresses)


class _GuardedHTTPAdapter(HTTPAdapter):
    """HTTPAdapter that re-validates the URL on every send (including redirects)."""

    def send(self, request, **kwargs):  # type: ignore[override]
        validate_public_url(request.url)
        return super().send(request, **kwargs)


def guarded_session(
    *,
    pool_maxsize: int = 10,
    retries: Optional[Retry] = None,
    user_agent: str = "Thestill/1.0",
) -> requests.Session:
    """
    Build a :class:`requests.Session` that validates every URL — including
    redirects — against :func:`validate_public_url`.

    Callers should always pass ``timeout=`` on the request; the session
    does not set a default timeout (requests has none by design).
    """
    session = requests.Session()
    retry = retries or Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = _GuardedHTTPAdapter(max_retries=retry, pool_connections=pool_maxsize, pool_maxsize=pool_maxsize)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent})
    session.max_redirects = _MAX_REDIRECTS
    return session


def guarded_get(url: str, **kwargs) -> requests.Response:
    """Convenience wrapper: validate, then GET via a short-lived guarded session."""
    validate_public_url(url)
    kwargs.setdefault("timeout", _DEFAULT_TIMEOUT_SECONDS)
    with guarded_session() as session:
        return session.get(url, **kwargs)


__all__ = [
    "UnsafeURLError",
    "ResolvedHost",
    "validate_public_url",
    "guarded_session",
    "guarded_get",
]
