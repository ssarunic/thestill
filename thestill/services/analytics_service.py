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

"""Layer A product analytics report (spec #95).

The one code path behind ``GET /api/dashboard/analytics`` and
``thestill analytics``: range → UTC window, the availability decision, and
assembling the repository reads into one report. No cache and no I/O
of its own; the route caches.
"""

from datetime import datetime, time, timedelta, timezone
from typing import Callable, Dict, Final, List

from structlog import get_logger

from ..models.analytics import AnalyticsReport, AnalyticsWindow
from ..repositories.analytics_repository import AnalyticsRepository, AnalyticsUnavailableError

logger = get_logger(__name__)

ALLOWED_RANGES: Final[Dict[str, int]] = {"7d": 7, "28d": 28, "90d": 90}
DEFAULT_RANGE: Final = "28d"

NOTES: Final[List[str]] = [
    "Admin accounts and the single-user local account are excluded.",
    "Each activation step counts only users who signed up at least that step's horizon ago.",
    "Reads and retention are lower bounds: an episode read and later saved or dismissed no longer counts as read.",
    "Active means an episode read, a show followed or a briefing opened.",
    "Briefing opens are recorded from spec #95 onwards; earlier ones count only if they were marked listened.",
]


# Rows per ranked content table (spec #103); the CLI prints the first 10.
CONTENT_LIMIT: Final = 20


class InvalidRangeError(ValueError):
    """Range is not one of ``ALLOWED_RANGES``."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AnalyticsService:
    """Read-only analytics over product tables."""

    def __init__(
        self,
        repository: AnalyticsRepository,
        *,
        enabled: bool,
        clock: Callable[[], datetime] = _utc_now,
    ):
        """
        Args:
            repository: Postgres implementation, or the unavailable stub.
            enabled: ``config.multi_user``; a single-user install has
                nobody to measure even on Postgres.
            clock: injectable "now" for deterministic tests.
        """
        self._repository = repository
        self._enabled = enabled
        self._clock = clock

    @property
    def available(self) -> bool:
        return self._enabled and self._repository.available

    def window_for(self, range_: str, *, now: datetime) -> AnalyticsWindow:
        """``range_`` whole UTC days ending with today, up to ``now``."""
        days = ALLOWED_RANGES.get(range_)
        if days is None:
            raise InvalidRangeError(f"range must be one of {', '.join(ALLOWED_RANGES)}")
        first_day = now.astimezone(timezone.utc).date() - timedelta(days=days - 1)
        start = datetime.combine(first_day, time.min, tzinfo=timezone.utc)
        return AnalyticsWindow(start=start, end=now, days=days)

    @staticmethod
    def previous_window(window: AnalyticsWindow) -> AnalyticsWindow:
        """The window of the same elapsed length ending at ``window.start``,
        so a part-day "now" compares like for like (spec #103)."""
        return AnalyticsWindow(start=window.start - (window.end - window.start), end=window.start, days=window.days)

    def report(self, range_: str = DEFAULT_RANGE) -> AnalyticsReport:
        """The full Layer A report for ``range_``.

        Raises:
            AnalyticsUnavailableError: when ``available`` is False.
            InvalidRangeError: when ``range_`` is not allowed.
        """
        if not self.available:
            raise AnalyticsUnavailableError("Product analytics is off (needs multi-user mode on Postgres)")
        now = self._clock()
        window = self.window_for(range_, now=now)
        report = AnalyticsReport(
            generated_at=now,
            range=range_,
            window=window,
            signups=self._repository.signups(window),
            activation=self._repository.activation(window),
            cohorts=self._repository.cohorts(now),
            engagement=self._repository.engagement(window),
            content=self._repository.content(window, self.previous_window(window), limit=CONTENT_LIMIT),
            notes=NOTES,
        )
        logger.info("analytics_report_built", range=range_, signups=report.signups.total)
        return report
