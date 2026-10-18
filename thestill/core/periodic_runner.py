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

"""A daemon-thread loop that runs small periodic jobs (spec #104).

Housekeeping (expiring abandoned quota reservations, pruning the usage
ledger) and, from Phase 3, the alert tick hang off this one loop instead of
each owning a thread. Every job must be idempotent: with several server
workers each runs its own loop.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from structlog import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PeriodicJob:
    name: str
    interval_seconds: float
    run: Callable[[], None]


class PeriodicRunner:
    def __init__(
        self,
        jobs: Sequence[PeriodicJob],
        *,
        tick_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jobs = list(jobs)
        self._tick = tick_seconds
        self._clock = clock
        self._last_run: Dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def run_due(self) -> List[str]:
        """Run every job whose interval has elapsed (all of them on the
        first call). A failing job is logged and never stops the others.
        Returns the names of the jobs that ran."""
        ran = []
        for job in self._jobs:
            now = self._clock()
            last = self._last_run.get(job.name)
            if last is not None and now - last < job.interval_seconds:
                continue
            self._last_run[job.name] = now
            ran.append(job.name)
            try:
                job.run()
            except Exception:
                logger.exception("periodic_job_failed", job=job.name)
        return ran

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="periodic-runner", daemon=True)
        self._thread.start()
        logger.info("periodic_runner_started", jobs=[job.name for job in self._jobs])

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        logger.info("periodic_runner_stopped")

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self.run_due()
            self._stop.wait(self._tick)
