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

"""Spec #49 Layer 1 — per-stage circuit breaker.

The healer loop (Layer 3) *cleans up* after an outage; the breaker *prevents*
the pile-up in the first place. When infrastructure-class failures (DNS, model
runtime, provider) breach a threshold within a rolling window for a stage, the
breaker **opens** that stage: the poll loop stops dequeuing new work, so the
queue pauses instead of grinding every in-flight task to death against a dead
dependency. After a cooldown the breaker goes **half-open** and lets a single
probe task through; its outcome decides whether to **close** (dependency back)
or re-**open** (still down).

Design constraints (spec Non-Goals): single process, in-memory, no persistence
— a restart re-probes from CLOSED, which is safe. State is mutated from two
contexts (the stage poll-loop coroutine calls ``allow_dispatch``; worker
threads call ``record_success``/``record_failure``), so every transition is
guarded by a lock. The clock is injectable for deterministic tests.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict

from structlog import get_logger

logger = get_logger(__name__)


class CircuitState(str, Enum):
    """Lifecycle of a single stage's breaker."""

    CLOSED = "closed"  # Normal — work flows.
    OPEN = "open"  # Tripped — dequeue paused until cooldown elapses.
    HALF_OPEN = "half_open"  # Cooldown elapsed — one probe task in flight.


@dataclass
class _Breaker:
    state: CircuitState = CircuitState.CLOSED
    # Monotonic timestamps of recent infra failures (rolling window).
    failures: Deque[float] = field(default_factory=deque)
    opened_at: float | None = None
    # True while a single half-open probe task is outstanding; blocks any
    # second probe from being dispatched until it resolves.
    probe_in_flight: bool = False


class StageCircuitBreaker:
    """Thread-safe per-stage circuit breaker keyed on the stage name.

    Spec open question #2 (key on stage vs underlying dependency) is resolved
    in favour of *stage*: it's the unit the poll loop already gates on, and for
    the current single-dependency-per-stage topology the two are equivalent.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        window_seconds: float = 120.0,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            failure_threshold: Infra failures within ``window_seconds`` that
                trip a CLOSED breaker OPEN.
            window_seconds: Rolling window over which failures are counted.
            cooldown_seconds: How long a breaker stays OPEN before a half-open
                probe is allowed.
            clock: Monotonic time source (injected for tests).
        """
        self._failure_threshold = max(1, failure_threshold)
        self._window_seconds = max(1.0, window_seconds)
        self._cooldown_seconds = max(0.0, cooldown_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._breakers: Dict[str, _Breaker] = {}

    def _get(self, stage: str) -> _Breaker:
        b = self._breakers.get(stage)
        if b is None:
            b = _Breaker()
            self._breakers[stage] = b
        return b

    def allow_dispatch(self, stage: str) -> bool:
        """Return True if the poller may dispatch one task for ``stage`` now.

        Reserves the half-open probe slot when it returns True in OPEN/HALF_OPEN
        — the caller MUST pair a True result with exactly one task dispatch, or
        call :meth:`cancel_dispatch` if it could not dequeue one (empty queue),
        so the reserved probe is not leaked.
        """
        now = self._clock()
        with self._lock:
            b = self._get(stage)
            if b.state == CircuitState.CLOSED:
                return True
            if b.state == CircuitState.OPEN:
                if b.opened_at is not None and now - b.opened_at >= self._cooldown_seconds:
                    # Cooldown elapsed → promote to half-open and reserve the
                    # single probe for this caller.
                    b.state = CircuitState.HALF_OPEN
                    b.probe_in_flight = True
                    logger.info("circuit_half_open", stage=stage)
                    return True
                return False
            # HALF_OPEN: only one probe at a time.
            if not b.probe_in_flight:
                b.probe_in_flight = True
                return True
            return False

    def cancel_dispatch(self, stage: str) -> None:
        """Release a probe reservation the poller could not use (empty queue).

        Reverts a just-promoted HALF_OPEN breaker back to OPEN and restarts the
        cooldown so the next probe waits a full interval rather than spinning.
        """
        now = self._clock()
        with self._lock:
            b = self._get(stage)
            if b.state == CircuitState.HALF_OPEN and b.probe_in_flight:
                b.probe_in_flight = False
                b.state = CircuitState.OPEN
                b.opened_at = now

    def record_failure(self, stage: str) -> CircuitState:
        """Record an infra-class failure; trip OPEN if the threshold is met.

        A failure during a half-open probe immediately re-opens. Returns the
        resulting state so the caller can decide whether to spend retry budget
        (only when the result is CLOSED).
        """
        now = self._clock()
        with self._lock:
            b = self._get(stage)

            if b.state == CircuitState.HALF_OPEN:
                # Probe failed → dependency still down. Re-open, reset cooldown.
                b.state = CircuitState.OPEN
                b.opened_at = now
                b.probe_in_flight = False
                b.failures.clear()
                logger.warning("circuit_reopened", stage=stage)
                return b.state

            # Slide the window and append.
            cutoff = now - self._window_seconds
            while b.failures and b.failures[0] < cutoff:
                b.failures.popleft()
            b.failures.append(now)

            if b.state == CircuitState.CLOSED and len(b.failures) >= self._failure_threshold:
                b.state = CircuitState.OPEN
                b.opened_at = now
                logger.warning(
                    "circuit_opened",
                    stage=stage,
                    failures=len(b.failures),
                    window_seconds=self._window_seconds,
                )
            return b.state

    def record_success(self, stage: str) -> None:
        """Record a successful task; closes a half-open breaker and clears the
        failure window for a closed one."""
        with self._lock:
            b = self._get(stage)
            if b.state != CircuitState.CLOSED:
                logger.info("circuit_closed", stage=stage, from_state=b.state.value)
            b.state = CircuitState.CLOSED
            b.opened_at = None
            b.probe_in_flight = False
            b.failures.clear()

    def state(self, stage: str) -> CircuitState:
        with self._lock:
            return self._get(stage).state

    def is_tripped(self, stage: str) -> bool:
        """True when the breaker is not CLOSED (OPEN or HALF_OPEN) — i.e. an
        infra failure here should NOT spend the task's retry budget."""
        return self.state(stage) != CircuitState.CLOSED

    def snapshot(self) -> Dict[str, str]:
        """Map of stage → state for any non-closed breaker (queue monitor)."""
        with self._lock:
            return {stage: b.state.value for stage, b in self._breakers.items() if b.state != CircuitState.CLOSED}
