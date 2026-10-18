"""Canned Layer A analytics for unit tests (spec #95).

The real SQL is covered by ``tests/integration/test_analytics_repository_contract.py``;
these fakes let the service, route and CLI tests run without Postgres.
"""

from datetime import date, datetime, timezone

from thestill.models.analytics import (
    COHORT_WEEKS,
    STEPS,
    ActivationGroup,
    ActivationReport,
    AnalyticsWindow,
    CategoryStat,
    CohortCell,
    CohortRow,
    CohortsReport,
    ContentReport,
    EngagementReport,
    EpisodeStat,
    PodcastStat,
    SignupDay,
    SignupsReport,
    StepStat,
)
from thestill.repositories.analytics_repository import AnalyticsRepository

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _group(source: str, signups: int) -> ActivationGroup:
    steps = [
        StepStat(
            key=s.key,
            label=s.label,
            horizon_hours=s.horizon_hours,
            eligible=signups,
            reached=1,
            pct=1 / signups,
            median_hours=2.0,
        )
        for s in STEPS
    ]
    return ActivationGroup(source=source, signups=signups, steps=steps)


class FakeAnalyticsRepository(AnalyticsRepository):
    """Records the windows it is asked for; returns fixed reports."""

    def __init__(self):
        self.calls = []

    def signups(self, window: AnalyticsWindow) -> SignupsReport:
        self.calls.append(("signups", window))
        day = SignupDay(day=date(2026, 10, 14), total=3, by_source={"episode": 2, "direct": 1})
        return SignupsReport(total=3, by_source={"episode": 2, "direct": 1}, days=[day])

    def activation(self, window: AnalyticsWindow) -> ActivationReport:
        self.calls.append(("activation", window))
        return ActivationReport(overall=_group("all", 3), by_source=[_group("episode", 2), _group("direct", 1)])

    def cohorts(self, as_of: datetime) -> CohortsReport:
        self.calls.append(("cohorts", as_of))
        cells = [CohortCell(week=w, eligible=6, active=3, pct=0.5) for w in COHORT_WEEKS]
        return CohortsReport(
            weeks=list(COHORT_WEEKS), rows=[CohortRow(week_start=date(2026, 8, 3), size=6, cells=cells)]
        )

    def engagement(self, window: AnalyticsWindow) -> EngagementReport:
        self.calls.append(("engagement", window))
        return EngagementReport(
            active_users=2,
            reads=8,
            reads_per_active_user_per_week=1.0,
            briefings_generated=4,
            briefings_opened=3,
            opened_ratio=0.75,
            follows=5,
            follows_per_active_user=2.5,
        )

    def content(self, window: AnalyticsWindow, previous: AnalyticsWindow, *, limit: int) -> ContentReport:
        self.calls.append(("content", (window, previous, limit)))
        return ContentReport(
            podcasts=[
                PodcastStat(
                    podcast_id="p1",
                    slug="hard-fork",
                    title="Hard Fork",
                    category="Technology",
                    followers=12,
                    new_follows=3,
                    follower_readers=6,
                    readers=9,
                    readers_previous=7,
                    listeners=4,
                    completions=2,
                ),
                PodcastStat(
                    podcast_id="p2",
                    slug="quiet-show",
                    title="Quiet Show",
                    category=None,
                    followers=20,
                    new_follows=0,
                    follower_readers=1,
                    readers=1,
                    readers_previous=0,
                    listeners=0,
                    completions=0,
                ),
            ],
            episodes=[
                EpisodeStat(
                    episode_id="e1",
                    slug="the-ai-episode",
                    title="The AI Episode",
                    podcast_slug="hard-fork",
                    podcast_title="Hard Fork",
                    published_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
                    imported=False,
                    readers=6,
                    listeners=3,
                    completions=1,
                )
            ],
            categories=[
                CategoryStat(
                    category="Technology", shows=4, shows_read=3, followers=15, readers=10, readers_previous=12
                ),
                CategoryStat(
                    category="Uncategorised", shows=1, shows_read=1, followers=20, readers=1, readers_previous=0
                ),
            ],
        )


class RecordingSink:
    """An enabled analytics sink that keeps what it was asked to capture."""

    enabled = True
    browser_key = "phc_test"

    def __init__(self):
        self.captured = []

    def capture(self, event, *, user=None, visitor_key=None, gpc=False):
        self.captured.append({"event": event, "user": user, "visitor_key": visitor_key, "gpc": gpc})

    def names(self):
        return [c["event"].name for c in self.captured]

    def shutdown(self):
        pass
