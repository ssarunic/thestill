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
