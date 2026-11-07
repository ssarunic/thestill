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
from typing import Callable, Dict, Final, List, Optional

from structlog import get_logger

from ..models.analytics import AnalyticsReport, AnalyticsWindow, ScorecardReport, TrafficReport
from ..repositories.analytics_repository import AnalyticsRepository, AnalyticsUnavailableError

logger = get_logger(__name__)

ALLOWED_RANGES: Final[Dict[str, int]] = {"7d": 7, "28d": 28, "90d": 90}
DEFAULT_RANGE: Final = "28d"

NOTES: Final[List[str]] = [
    "Admin accounts and the single-user local account are excluded.",
    "Each activation step counts only users who signed up at least that step's horizon ago.",
    "Reads and retention are lower bounds: an episode read and later saved or dismissed no longer counts as read.",
    "Active means a value day (spec #111): an episode read or opened, audio played, a briefing opened or a connector call. A follow is setup, so it doesn't count.",
    "Briefing opens are recorded from spec #95 onwards; earlier ones count only if they were marked listened.",
]


# Rows per ranked content table (spec #103); the CLI prints the first 10.
CONTENT_LIMIT: Final = 20

# Top public pages in the traffic section (spec #115 D5).
TRAFFIC_LIMIT: Final = 20


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
        entity_path: Optional[Callable[[str], Optional[str]]] = None,
    ):
        """
        Args:
            repository: Postgres implementation, or the unavailable stub.
            enabled: ``config.multi_user``; a single-user install has
                nobody to measure even on Postgres.
            clock: injectable "now" for deterministic tests.
            entity_path: entity id → the public path that shows it
                (``api_entities.entity_page_path``). The repository's path
                assumes the entity owns its current-type URL; a re-typed
                entity may not (spec #115). None keeps that path.
        """
        self._repository = repository
        self._enabled = enabled
        self._clock = clock
        self._entity_path = entity_path

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
        self._require_available()
        now = self._clock()
        window = self.window_for(range_, now=now)
        report = AnalyticsReport(
            generated_at=now,
            range=range_,
            window=window,
            scorecard=self._repository.scorecard(now),
            signups=self._repository.signups(window),
            activation=self._repository.activation(window),
            cohorts=self._repository.cohorts(now),
            engagement=self._repository.engagement(window),
            content=self._repository.content(window, self.previous_window(window), limit=CONTENT_LIMIT),
            traffic=self._resolve_entity_paths(self._repository.traffic(window, limit=TRAFFIC_LIMIT)),
            notes=NOTES,
        )
        logger.info("analytics_report_built", range=range_, signups=report.signups.total)
        return report

    def scorecard(self) -> ScorecardReport:
        """Only the spec #111 Scorecard (``thestill analytics --scorecard``).

        Raises:
            AnalyticsUnavailableError: when ``available`` is False.
        """
        self._require_available()
        return self._repository.scorecard(self._clock())

    def _resolve_entity_paths(self, traffic: TrafficReport) -> TrafficReport:
        """Point each listed entity page at the URL that actually shows it."""
        if self._entity_path is None:
            return traffic
        for page in traffic.top_pages:
            if page.kind != "entity" or page.title is None:
                continue
            try:
                page.path = self._entity_path(page.object_id)
            except Exception:  # noqa: BLE001 - a link never breaks the report
                logger.warning("analytics_entity_path_failed", entity_id=page.object_id, exc_info=True)
        return traffic

    def _require_available(self) -> None:
        if not self.available:
            raise AnalyticsUnavailableError("Product analytics is off (needs multi-user mode on Postgres)")
