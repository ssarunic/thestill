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
An ``https`` address for an episode whose enclosure is plain ``http``.

The reader is an ``https`` page, and a browser will not load ``http`` media
into one. Most browsers retry the same address over ``https`` at every
redirect hop, which works for most hosts. It does not work everywhere: the
BBC's download host sends a Safari user agent to an ``http`` address for the
last hop and refuses that file over ``https``, so in Safari the audio never
loads.

This module follows the enclosure's redirects itself, over ``https`` at
every hop, and returns the address of the audio file. That address serves
any browser. The audio is still downloaded by the listener from the
publisher's host; only the redirect hops are made from here. Nothing about
the listener is sent: the requests carry Thestill's own user agent and ask
for one byte.

The file address is usually signed and short-lived, so it is resolved when
playback asks for it and never stored. It is remembered per listener for as
long as its signature allows (four hours at most): a player asks again on
every seek, and a host that splices ads may hand out a different copy of the
episode each time it is asked. One listener must keep one copy for the whole
play. Listeners do not share an address, so the host still sees one request
per listener.
"""

from __future__ import annotations

import calendar
import threading
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import requests
from structlog import get_logger

from ..utils.url_guard import UnsafeURLError, validate_public_url

logger = get_logger(__name__)

USER_AGENT = "Thestill/1.0"
MAX_HOPS = 8
#: Connect and read timeouts, per hop. A range seek waits on this.
TIMEOUT_S: Tuple[float, float] = (4.0, 6.0)
#: The whole walk must answer before the player gives up on the audio
#: (15 s), whatever the hop count.
DEADLINE_S = 10.0
#: How long a failure is remembered, so a player retrying its range
#: requests does not walk an unreachable chain each time.
FAILURE_TTL_S = 15.0
#: How long a resolved address is reused when it does not say when it
#: expires.
DEFAULT_TTL_S = 300.0
#: The longest any address is reused, and how long before its stated expiry
#: it is dropped (a range request made near the end must still succeed).
MAX_TTL_S = 4 * 60 * 60.0
EXPIRY_MARGIN_S = 15 * 60.0
#: A short-lived signature gives up a quarter of its life, not the full
#: margin (which would leave it nothing).
EXPIRY_MARGIN_SHARE = 0.25
CACHE_MAX_ENTRIES = 512

_REDIRECTS = (301, 302, 303, 307, 308)


def _https(url: str) -> str:
    """``url`` over ``https``: scheme replaced (any case), an explicit port 80
    dropped (it is the plain-http port; TLS is not spoken there)."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "http":
        return url.strip()
    netloc = parts.netloc[: -len(":80")] if parts.netloc.endswith(":80") else parts.netloc
    return urlunsplit(("https", netloc, parts.path, parts.query, parts.fragment))


def _scheme(url: str) -> str:
    return urlsplit(url.strip()).scheme.lower()


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _signed_at(value: str) -> Optional[float]:
    """The epoch second of a signing date such as ``20261112T200000Z``."""
    try:
        return float(calendar.timegm(time.strptime(value, "%Y%m%dT%H%M%SZ")))
    except ValueError:
        return None


def _ttl(resolved: str, wall_now: float) -> float:
    """How long ``resolved`` may be reused.

    A signed address says when it stops working. ``Expires`` is an epoch
    second. ``X-Amz-Expires`` / ``X-Goog-Expires`` are a number of seconds
    from the signing time in ``X-Amz-Date`` / ``X-Goog-Date``: an address
    signed a while ago has that much less left, and without a readable
    signing time the number says nothing. The address is dropped a margin
    before it expires, within the cap. One that states no expiry gets the
    default.
    """
    query = {name.lower(): value for name, value in parse_qsl(urlsplit(resolved).query)}
    remaining: Optional[float] = None
    if query.get("expires", "").isdigit():
        remaining = int(query["expires"]) - wall_now
    else:
        for prefix in ("x-amz", "x-goog"):
            lifetime = query.get(f"{prefix}-expires", "")
            signed_at = _signed_at(query.get(f"{prefix}-date", ""))
            if lifetime.isdigit() and signed_at is not None:
                remaining = signed_at + int(lifetime) - wall_now
                break
    if remaining is None:
        return DEFAULT_TTL_S
    margin = min(EXPIRY_MARGIN_S, remaining * EXPIRY_MARGIN_SHARE)
    return max(0.0, min(MAX_TTL_S, remaining - margin))


class AudioAddressResolver:
    """Resolves and briefly remembers the ``https`` file address of an enclosure."""

    def __init__(
        self,
        get_fn: Callable[..., requests.Response] = requests.get,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._get = get_fn
        self._clock = clock
        self._lock = threading.Lock()
        # (enclosure, listener) -> (address or None for a failure, good until)
        self._cache: Dict[Tuple[str, str], Tuple[Optional[str], float]] = {}
        # One walk at a time per (enclosure, listener): a player opens with
        # several range requests at once, and each walk of an ad-splicing
        # host may come back with a different copy.
        self._walking: Dict[Tuple[str, str], threading.Event] = {}

    def playable_url(self, audio_url: str, listener: str = "") -> Optional[str]:
        """The address to hand a browser on an ``https`` page, or ``None``.

        An ``https`` enclosure is returned as it is, with no request made.
        ``None`` means no ``https`` address of the audio could be reached.
        ``listener`` only keys the memory of earlier answers; it is never
        sent anywhere.
        """
        scheme = _scheme(audio_url)
        if scheme == "https":
            return audio_url
        if scheme != "http":
            return None
        key = (audio_url, listener)
        while True:
            with self._lock:
                cached = self._cache.get(key)
                if cached and cached[1] > self._clock():
                    return cached[0]
                waiting = self._walking.get(key)
                if waiting is None:
                    mine = self._walking[key] = threading.Event()
                    break
            # Someone is already walking this chain for this listener:
            # take their answer.
            waiting.wait(DEADLINE_S + TIMEOUT_S[0] + TIMEOUT_S[1])
        try:
            resolved = self._resolve(audio_url)
            # After the walk: its seconds count against the address's life.
            now = self._clock()
            ttl = _ttl(resolved, now) if resolved is not None else FAILURE_TTL_S
            with self._lock:
                if ttl > 0:
                    self._remember(key, resolved, now + ttl, now)
            return resolved
        finally:
            with self._lock:
                self._walking.pop(key, None)
            mine.set()

    def _remember(self, key: Tuple[str, str], value: Optional[str], until: float, now: float) -> None:
        self._cache.pop(key, None)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            # Make room without taking a listener's address away mid-play:
            # first what has expired, then the oldest entry.
            for stale in [k for k, v in self._cache.items() if v[1] <= now]:
                del self._cache[stale]
            while len(self._cache) >= CACHE_MAX_ENTRIES:
                del self._cache[next(iter(self._cache))]
        self._cache[key] = (value, until)

    def _resolve(self, audio_url: str) -> Optional[str]:
        current = _https(audio_url)
        started = time.monotonic()
        try:
            for hop in range(MAX_HOPS + 1):
                left = DEADLINE_S - (time.monotonic() - started)
                if left <= 0:
                    logger.warning(
                        "audio_address_unresolved", source_host=_host(audio_url), reason="deadline", hops=hop
                    )
                    return None
                # Every hop is checked: the enclosure comes from a feed, and a
                # redirect must not reach an internal address from here.
                validate_public_url(current)
                response = self._get(
                    current,
                    headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
                    allow_redirects=False,
                    stream=True,
                    timeout=(min(TIMEOUT_S[0], left), min(TIMEOUT_S[1], left)),
                )
                status = response.status_code
                location = (response.headers.get("Location") or "").strip()
                response.close()
                if status in _REDIRECTS and location:
                    current = _https(urljoin(current, location))
                    continue
                if status in (200, 206):
                    logger.info(
                        "audio_address_resolved", source_host=_host(audio_url), file_host=_host(current), hops=hop
                    )
                    return current
                logger.warning(
                    "audio_address_unresolved",
                    source_host=_host(audio_url),
                    stopped_at_host=_host(current),
                    status=status,
                    hops=hop,
                )
                return None
            logger.warning("audio_address_unresolved", source_host=_host(audio_url), reason="too_many_redirects")
        except UnsafeURLError as exc:
            logger.warning(
                "audio_address_unresolved", source_host=_host(audio_url), reason="unsafe_url", error=str(exc)
            )
        except requests.RequestException as exc:
            logger.warning(
                "audio_address_unresolved",
                source_host=_host(audio_url),
                stopped_at_host=_host(current),
                reason="request_failed",
                error=type(exc).__name__,
            )
        return None


#: One resolver per process: the cache is the point.
resolver = AudioAddressResolver()

__all__ = ["AudioAddressResolver", "resolver", "USER_AGENT"]
