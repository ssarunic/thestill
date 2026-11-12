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

import threading
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urljoin, urlsplit

import requests
from structlog import get_logger

from ..utils.url_guard import UnsafeURLError, validate_public_url

logger = get_logger(__name__)

USER_AGENT = "Thestill/1.0"
MAX_HOPS = 8
#: Connect and read timeouts, per hop. A range seek waits on this.
TIMEOUT_S: Tuple[float, float] = (4.0, 6.0)
#: How long a resolved address is reused when it does not say when it
#: expires.
DEFAULT_TTL_S = 300.0
#: The longest any address is reused, and how long before its stated expiry
#: it is dropped (a range request made near the end must still succeed).
MAX_TTL_S = 4 * 60 * 60.0
EXPIRY_MARGIN_S = 15 * 60.0
CACHE_MAX_ENTRIES = 512

_REDIRECTS = (301, 302, 303, 307, 308)


def _https(url: str) -> str:
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _ttl(resolved: str, wall_now: float) -> float:
    """How long ``resolved`` may be reused: until its ``Expires`` (the epoch
    second a signed address carries), less a margin, within the cap."""
    for name, value in parse_qsl(urlsplit(resolved).query):
        if name.lower() == "expires" and value.isdigit():
            return max(0.0, min(MAX_TTL_S, int(value) - wall_now - EXPIRY_MARGIN_S))
    return DEFAULT_TTL_S


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
        self._cache: Dict[Tuple[str, str], Tuple[str, float]] = {}

    def playable_url(self, audio_url: str, listener: str = "") -> Optional[str]:
        """The address to hand a browser on an ``https`` page, or ``None``.

        An ``https`` enclosure is returned as it is, with no request made.
        ``None`` means no ``https`` address of the audio could be reached.
        ``listener`` only keys the memory of earlier answers; it is never
        sent anywhere.
        """
        if audio_url.startswith("https://"):
            return audio_url
        if not audio_url.startswith("http://"):
            return None
        key = (audio_url, listener)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
        resolved = self._resolve(audio_url)
        if resolved is not None:
            ttl = _ttl(resolved, now)
            with self._lock:
                if len(self._cache) >= CACHE_MAX_ENTRIES:
                    # Drop what has expired; if that is not enough, start over.
                    self._cache = {k: v for k, v in self._cache.items() if v[1] > now}
                    if len(self._cache) >= CACHE_MAX_ENTRIES:
                        self._cache.clear()
                if ttl > 0:
                    self._cache[key] = (resolved, now + ttl)
        return resolved

    def _resolve(self, audio_url: str) -> Optional[str]:
        current = _https(audio_url)
        try:
            for hop in range(MAX_HOPS + 1):
                # Every hop is checked: the enclosure comes from a feed, and a
                # redirect must not reach an internal address from here.
                validate_public_url(current)
                response = self._get(
                    current,
                    headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"},
                    allow_redirects=False,
                    stream=True,
                    timeout=TIMEOUT_S,
                )
                status = response.status_code
                location = response.headers.get("Location", "")
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
