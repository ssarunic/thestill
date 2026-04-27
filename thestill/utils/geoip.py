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

"""IP-to-country geolocation.

Used to infer a user's region (ISO 3166-1 alpha-2) on first login so
region-scoped content (e.g. top podcasts) defaults to a sensible market.
The user can always override the inferred value, after which inference
is suppressed.

Implementation choice — we hit ipinfo.io's keyless ``/{ip}/country``
endpoint. It returns a 2-char body like ``"US\\n"``, has a generous free
tier, and needs no API key. The lookup is best-effort: a timeout, network
error, or non-public IP all resolve to ``None`` and the caller should
fall back to its own default. We intentionally do not cache — region is
inferred at most once per user and then persisted on the user row.
"""

from __future__ import annotations

import ipaddress
from typing import Optional

import httpx
from structlog import get_logger

logger = get_logger(__name__)

# ipinfo.io's free, keyless country endpoint.
_IPINFO_COUNTRY_URL = "https://ipinfo.io/{ip}/country"
_LOOKUP_TIMEOUT_SECONDS = 2.0


def _is_routable_public_ip(ip: str) -> bool:
    """True only for public, globally-routable IPv4/IPv6 addresses.

    Filters localhost, private ranges, link-local, etc. — looking these
    up wastes a network round-trip and ipinfo returns no useful data.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    )


async def lookup_country_from_ip(ip: Optional[str]) -> Optional[str]:
    """Best-effort IP→ISO 3166-1 alpha-2 lookup, lowercased.

    Returns ``None`` for missing / private / unroutable IPs and on any
    network or parsing error. Never raises.
    """
    if not ip or not _is_routable_public_ip(ip):
        return None

    url = _IPINFO_COUNTRY_URL.format(ip=ip)
    try:
        async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("geoip_lookup_failed", ip=ip, error=str(exc))
        return None

    if response.status_code != 200:
        logger.debug("geoip_lookup_non_200", ip=ip, status=response.status_code)
        return None

    code = response.text.strip().lower()
    # ipinfo returns "undefined" or empty for unknown IPs.
    if len(code) != 2 or not code.isalpha():
        return None
    return code
