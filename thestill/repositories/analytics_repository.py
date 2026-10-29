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

"""Read-only Layer A analytics over product tables (spec #95).

Postgres only. Hosted multi-user runs on Postgres; a SQLite install is a
single-user self-host with nothing to measure, so it gets
:class:`UnavailableAnalyticsRepository` instead of a second implementation.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from ..models.analytics import (
    ActivationReport,
    AnalyticsWindow,
    CohortsReport,
    ContentReport,
    EngagementReport,
    ScorecardReport,
    SignupsReport,
)


class AnalyticsUnavailableError(RuntimeError):
    """Raised by the stub; callers check ``available`` first."""


class AnalyticsRepository(ABC):
    """Each method is one SQL statement over existing tables."""

    available: bool = True

    @abstractmethod
    def signups(self, window: AnalyticsWindow) -> SignupsReport:
        """Signups per UTC day in ``window``, split by signup source."""

    @abstractmethod
    def activation(self, window: AnalyticsWindow) -> ActivationReport:
        """Funnel steps for users created in ``window``, overall and per
        source, with maturity-gated denominators against ``window.end``."""

    @abstractmethod
    def cohorts(self, as_of: datetime) -> CohortsReport:
        """Retention for the last ``COHORT_ROWS`` signup weeks up to
        ``as_of``; independent of the selected range."""

    @abstractmethod
    def engagement(self, window: AnalyticsWindow) -> EngagementReport:
        """Reads, briefings and follows in ``window``."""

    @abstractmethod
    def scorecard(self, as_of: datetime) -> ScorecardReport:
        """The spec #111 Scorecard: four weekly series, the Layer A
        guardrails and the opted-out count, up to ``as_of``; independent of
        the selected range. One statement per series."""

    @abstractmethod
    def content(self, window: AnalyticsWindow, previous: AnalyticsWindow, *, limit: int) -> ContentReport:
        """Top podcasts, top episodes and categories for ``window``, with
        readers in ``previous`` for the change column (spec #103). One
        statement per table."""


class UnavailableAnalyticsRepository(AnalyticsRepository):
    """Stand-in for backends with no analytics (SQLite)."""

    available = False

    def _unavailable(self):
        raise AnalyticsUnavailableError("Product analytics needs the Postgres backend")

    def signups(self, window: AnalyticsWindow) -> SignupsReport:
        self._unavailable()

    def activation(self, window: AnalyticsWindow) -> ActivationReport:
        self._unavailable()

    def cohorts(self, as_of: datetime) -> CohortsReport:
        self._unavailable()

    def engagement(self, window: AnalyticsWindow) -> EngagementReport:
        self._unavailable()

    def scorecard(self, as_of: datetime) -> ScorecardReport:
        self._unavailable()

    def content(self, window: AnalyticsWindow, previous: AnalyticsWindow, *, limit: int) -> ContentReport:
        self._unavailable()
