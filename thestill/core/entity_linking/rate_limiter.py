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

"""Process-wide pacing for outbound Wikidata requests (spec #81).

The worker runs several resolve-entities tasks in threads, and they
share one budget with Wikimedia, so the limiter is shared too. A
Retry-After from any request holds every caller back, not only the
thread that received it.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional


class WikidataRateLimiter:
    """Evenly spaced slots at max_rps, plus a shared hold-off."""

    def __init__(
        self,
        max_rps: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_rps <= 0:
            raise ValueError(f"max_rps must be positive, got {max_rps}")
        self._interval = 1.0 / max_rps
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self._held_until = 0.0

    def acquire(self) -> None:
        """Block until this caller's slot. Slots are handed out under the
        lock; the wait happens outside it so callers queue, not convoy.

        A caller that wakes to find a hold-off arrived while it slept gives
        its slot up and queues again behind the new deadline. Otherwise the
        callers already waiting would all fire into the 429 that another
        worker was just told to back off from.
        """
        while True:
            with self._lock:
                now = self._clock()
                slot = max(now, self._next_slot, self._held_until)
                self._next_slot = slot + self._interval
            if slot > now:
                self._sleep(slot - now)
            with self._lock:
                if self._clock() >= self._held_until:
                    return

    def hold_off(self, seconds: float) -> None:
        """Nobody sends before ``seconds`` from now (``Retry-After``),
        including callers already waiting on a slot."""
        if seconds <= 0:
            return
        with self._lock:
            self._held_until = max(self._held_until, self._clock() + seconds)


_shared: Optional[WikidataRateLimiter] = None
_shared_lock = threading.Lock()


def get_shared_rate_limiter(max_rps: float) -> WikidataRateLimiter:
    """The one limiter for this process. The first caller's rate wins."""
    global _shared  # pylint: disable=global-statement
    with _shared_lock:
        if _shared is None:
            _shared = WikidataRateLimiter(max_rps)
        return _shared


def reset_shared_rate_limiter() -> None:
    """Test hook."""
    global _shared  # pylint: disable=global-statement
    with _shared_lock:
        _shared = None
