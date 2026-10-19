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

"""Spec #104 — recent per-user rate-limit hits, for ``rate_limit_pressure``.

The web limiter records a hit each time it refuses a user; the alert tick
reads the counts. In-process only: each worker sees its own hits, so the
alert threshold is a per-worker lower bound, and a process that serves no
web traffic (the CLI) sees none.
"""

import threading
import time
from collections import deque
from typing import Deque, Dict

HORIZON_SECONDS = 3600
# Past this many users, a record() also sweeps everyone's expired hits.
_SWEEP_TRIGGER = 1024


class UserHitLog:
    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def record(self, user_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(user_id, deque())
            hits.append(now)
            # Trim this user's own history every time, so one flooding user
            # can't grow it without bound between sweeps.
            while now - hits[0] > HORIZON_SECONDS:
                hits.popleft()
            if len(self._hits) > _SWEEP_TRIGGER:
                self._sweep(now)

    def counts(self, window_seconds: int = HORIZON_SECONDS) -> Dict[str, int]:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            return {uid: sum(1 for t in hits if now - t <= window_seconds) for uid, hits in self._hits.items()}

    def _sweep(self, now: float) -> None:
        for uid in list(self._hits):
            hits = self._hits[uid]
            while hits and now - hits[0] > HORIZON_SECONDS:
                hits.popleft()
            if not hits:
                del self._hits[uid]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


USER_HITS = UserHitLog()


def user_rate_limit_hits(window_seconds: int = HORIZON_SECONDS) -> Dict[str, int]:
    """Per-user rate-limit hits in this process over the last ``window_seconds``."""
    return USER_HITS.counts(window_seconds)
