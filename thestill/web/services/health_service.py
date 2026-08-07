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

"""Readiness checks for the web layer (spec #66).

Web-only service (a compose/ALB probe concern, not business logic shared
with CLI/MCP), hence ``web/services/`` rather than ``thestill/services/``.
HTTP-agnostic: raises on failure, returns None on success; the route maps
that to 200/503.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...repositories import factory

if TYPE_CHECKING:
    from ...utils.config import Config


class HealthService:
    """Dependency probes behind ``/health/ready``."""

    def __init__(self, config: "Config") -> None:
        self._config = config

    def ping_database(self) -> None:
        """One cheap round-trip against the configured backend. Raises on
        failure (connection refused, missing file, wedged server)."""
        factory.ping(self._config)

    def check_worker(self, task_worker) -> None:
        """Raise when the task worker has degraded into not claiming.

        The 2026-08-07 outage ran for hours with every probe green: liveness
        only proves the process answers HTTP, and readiness only proved the
        database was reachable. Neither looks at the thing that actually does
        the work. A worker that has leaked its executor slack is not ready —
        it is a web server with a dead pipeline behind it.
        """
        if task_worker is None:
            return
        is_degraded = getattr(task_worker, "is_degraded", None)
        if callable(is_degraded) and is_degraded():
            raise RuntimeError("task worker degraded: leaked handler threads; restart required")
