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
Client ID Metadata Documents (spec #109).

When an OAuth ``client_id`` is an ``https://`` URL, the client is the JSON
document at that URL (Claude and Claude Code identify themselves this way;
the MCP SDK models the field but implements nothing server-side). The
fetch runs against a URL any caller of ``/authorize`` chooses, so it is
fenced: https only, no credentials in the URL, every resolved address must
be public, no redirects, a 5 s timeout and a 64 KB cap. Fetched documents
are cached in ``oauth_clients`` for their ``Cache-Control`` max-age, capped
at 24 h; a failed refetch keeps serving the last good copy.

The address check runs before the fetch and the HTTP client resolves the
name again, so a DNS answer that changes in between (rebinding) is not
excluded. What such a fetch can do is limited: a GET without credentials
whose body is only ever parsed as client metadata, never echoed back.
"""

import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

import httpx
from structlog import get_logger

from ..models.oauth import OAuthClientRecord
from ..repositories.oauth_repository import OAuthRepository
from ..utils.datetime_utils import now_utc

logger = get_logger(__name__)

MAX_BYTES = 64 * 1024
TIMEOUT_SECONDS = 5.0
MAX_CACHE = timedelta(hours=24)
# A document with no usable ``Cache-Control`` is refetched after this.
DEFAULT_CACHE = timedelta(hours=1)
_MAX_AGE = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)


class CimdError(Exception):
    """The URL or the document at it is not an acceptable client."""


@dataclass(frozen=True)
class FetchedDocument:
    document: Dict[str, Any]
    max_age: Optional[timedelta]


def is_cimd_client_id(client_id: str) -> bool:
    """True when ``client_id`` names a metadata document rather than a registration."""
    return client_id.startswith("https://")


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise CimdError("client_id must be an https URL")
    if parts.username or parts.password or parts.fragment:
        raise CimdError("client_id URL must not carry credentials or a fragment")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise CimdError("client_id host does not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise CimdError("client_id host resolves to a non-public address")
    return parts.hostname


def fetch_document(url: str) -> FetchedDocument:
    """GET the metadata document at ``url`` within the fences above."""
    _check_url(url)
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                if response.status_code != 200:
                    raise CimdError(f"metadata document answered {response.status_code}")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise CimdError("metadata document is too large")
                cache_control = response.headers.get("cache-control", "")
    except httpx.HTTPError as exc:
        raise CimdError("metadata document could not be fetched") from exc
    try:
        document = json.loads(body)
    except ValueError as exc:
        raise CimdError("metadata document is not JSON") from exc
    if not isinstance(document, dict):
        raise CimdError("metadata document is not a JSON object")
    match = _MAX_AGE.search(cache_control)
    no_store = "no-store" in cache_control.lower() or "no-cache" in cache_control.lower()
    max_age = timedelta(0) if no_store else (timedelta(seconds=int(match.group(1))) if match else None)
    return FetchedDocument(document=document, max_age=max_age)


def validate_document(url: str, document: Dict[str, Any]) -> Dict[str, Any]:
    """The document if it can describe the client at ``url``, else ``CimdError``.

    Scope is deliberately not checked here: a document may omit it, and the
    scope policy is applied when the record becomes an SDK client.
    """
    if document.get("client_id") != url:
        raise CimdError("metadata document client_id does not match its URL")
    redirect_uris = document.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris or not all(isinstance(u, str) for u in redirect_uris):
        raise CimdError("metadata document has no redirect_uris")
    method = document.get("token_endpoint_auth_method", "none")
    if method != "none":
        # A metadata document has no secret to share; only public clients
        # (and private_key_jwt, which we do not support) can use one.
        raise CimdError(f"token_endpoint_auth_method {method!r} is not supported for a metadata document")
    return {**document, "token_endpoint_auth_method": "none"}


class CimdResolver:
    """Resolve a URL ``client_id`` to a client record, through the cache."""

    def __init__(
        self,
        repository: OAuthRepository,
        *,
        fetch: Callable[[str], FetchedDocument] = fetch_document,
        clock: Callable = now_utc,
    ):
        self._repository = repository
        self._fetch = fetch
        self._clock = clock

    def resolve(self, url: str) -> Optional[OAuthClientRecord]:
        now = self._clock()
        cached = self._repository.get_client(url)
        if cached is not None and cached.source == "cimd" and cached.fresh_until and cached.fresh_until > now:
            return cached
        try:
            fetched = self._fetch(url)
            document = validate_document(url, fetched.document)
        except CimdError as exc:
            logger.warning("oauth_cimd_fetch_failed", client_id=url, error=str(exc), cached=cached is not None)
            return cached if cached is not None and cached.source == "cimd" else None
        max_age = fetched.max_age if fetched.max_age is not None else DEFAULT_CACHE
        record = OAuthClientRecord(
            client_id=url,
            source="cimd",
            client_name=document.get("client_name") if isinstance(document.get("client_name"), str) else None,
            metadata=document,
            created_at=cached.created_at if cached is not None else now,
            fetched_at=now,
            fresh_until=now + min(max_age, MAX_CACHE),
        )
        self._repository.save_client(record)
        return record
