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

"""Layer A product analytics: the funnel derived from product tables (spec #95).

Every number here is read from state the product already keeps (users,
follows, inbox read state, briefings). Nothing is collected for it. Admin
accounts and the single-user ``local`` account are excluded throughout.
"""

from datetime import date, datetime
from typing import Dict, List, NamedTuple, Optional

from pydantic import BaseModel, Field, computed_field

# ``users.signup_source IS NULL``: accounts created before it was tracked.
BEFORE_TRACKING = "before_tracking"

# Groups smaller than this are shown greyed out (spec #95 decision D1).
SMALL_GROUP = 5

# Category row for shows with no primary category (spec #103).
UNCATEGORISED = "Uncategorised"

# What the Content numbers can't see (spec #103 Phase 1), shown under its tables.
CONTENT_NOTES = (
    "A reader opened an episode (from any page) or listened to it. Readers who turned usage "
    "analytics off, or whose browser sends Global Privacy Control, count only as followers.",
    "Opens before the views table existed were copied from inbox reads: they miss episodes "
    'opened outside the inbox and may include a few "Mark as played" clicks.',
    "Listening counts the latest playback per user and episode, so a listen in this window hides "
    "an earlier one: the change column is a trend, not an exact count.",
    "Followers is the current count; new follows are follows made in the window that still exist.",
)


class StepDef(NamedTuple):
    """One activation step. A user counts towards it only once they signed
    up at least ``horizon_hours`` ago (maturity gate), and reaches it when
    ``fact`` happened within that horizon of signup."""

    key: str
    label: str
    horizon_hours: int
    fact: str  # the per-user moment measured: first_follow, first_briefing, first_opened, third_read


# The activation funnel, in order. The single definition of the steps: the
# repository, the API payload (``StepStat.label``), the page and the CLI
# all read it.
STEPS = (
    StepDef("follow_24h", "Followed a show within 24 h", 24, "first_follow"),
    StepDef("follow_7d", "Followed a show within 7 days", 7 * 24, "first_follow"),
    StepDef("briefing_generated", "First briefing within 7 days", 7 * 24, "first_briefing"),
    StepDef("briefing_opened", "Opened first briefing within 7 days", 7 * 24, "first_opened"),
    StepDef("reads_3_first_week", "Read 3+ episodes in week 1", 7 * 24, "third_read"),
)

# Activity weeks shown in the retention table. Week k is days [7k, 7k+7)
# after the user's own signup, so week 0 (the signup week) is never shown.
COHORT_WEEKS = (1, 2, 4, 8)
COHORT_ROWS = 12


class AnalyticsWindow(BaseModel):
    """Half-open UTC window ``[start, end)``; ``end`` is "now"."""

    start: datetime
    end: datetime
    days: int


class SignupDay(BaseModel):
    day: date
    total: int
    by_source: Dict[str, int]


class SignupsReport(BaseModel):
    total: int
    by_source: Dict[str, int]
    days: List[SignupDay]  # every day of the window, zero-filled


class StepStat(BaseModel):
    key: str
    label: str
    horizon_hours: int
    eligible: int  # users old enough for this step
    reached: int
    pct: Optional[float]  # None when eligible == 0
    median_hours: Optional[float]  # None when reached == 0


class ActivationGroup(BaseModel):
    source: str  # "all", a signup source, or BEFORE_TRACKING
    signups: int
    steps: List[StepStat]


class ActivationReport(BaseModel):
    overall: ActivationGroup
    by_source: List[ActivationGroup]


class CohortCell(BaseModel):
    week: int
    eligible: int  # cohort members whose week ``week`` has fully passed
    active: int
    pct: Optional[float]  # None until at least one member is eligible


class CohortRow(BaseModel):
    week_start: date  # Monday, UTC
    size: int
    cells: List[CohortCell]


class CohortsReport(BaseModel):
    weeks: List[int]
    rows: List[CohortRow]  # the last COHORT_ROWS signup weeks, oldest first


class EngagementReport(BaseModel):
    active_users: int
    reads: int
    reads_per_active_user_per_week: Optional[float]
    briefings_generated: int
    briefings_opened: int
    opened_ratio: Optional[float]
    follows: int
    follows_per_active_user: Optional[float]


def change_label(now: int, before: int) -> str:
    """Readers against the previous window: ``+3``, ``−2`` (true minus),
    ``0``, ``new`` when the previous window had none, ``—`` when neither had.
    The one formatter: the API sends it as ``change``, the CLI prints it."""
    if before == 0:
        return "new" if now else "—"
    diff = now - before
    return f"{diff:+d}".replace("-", "−") if diff else "0"


class PodcastStat(BaseModel):
    """One show's readers and follows in the window (spec #103)."""

    podcast_id: str
    slug: str
    title: str
    category: Optional[str]  # top-level name; None = uncategorised
    followers: int
    new_follows: int  # surviving follows created in the window
    follower_readers: int  # followers who were readers in the window
    readers: int
    readers_previous: int
    listeners: int
    completions: int
    public_views: int = 0  # signed-out views of its show and episode pages (Phase 2)

    @computed_field
    @property
    def change(self) -> str:
        return change_label(self.readers, self.readers_previous)


class EpisodeStat(BaseModel):
    episode_id: str
    slug: str
    title: str
    podcast_slug: str
    podcast_title: str
    published_at: Optional[datetime]
    imported: bool  # synthetic parent (spec #31)
    readers: int
    listeners: int
    completions: int
    public_views: int = 0  # signed-out views of its public page (Phase 2)


class CategoryStat(BaseModel):
    category: str  # top-level name, or UNCATEGORISED
    shows: int  # shows with at least one follower
    shows_read: int  # shows with at least one reader in the window
    followers: int  # distinct users following any show in it
    readers: int
    readers_previous: int
    public_views: int = 0  # its shows' public views (Phase 2)

    @computed_field
    @property
    def change(self) -> str:
        return change_label(self.readers, self.readers_previous)


class ContentReport(BaseModel):
    """What readers read (spec #103): podcasts (the union of the top
    ``limit`` by readers and by followers), episodes, categories."""

    podcasts: List[PodcastStat]
    episodes: List[EpisodeStat]
    categories: List[CategoryStat]
    notes: List[str] = Field(default_factory=lambda: list(CONTENT_NOTES))


class AnalyticsReport(BaseModel):
    generated_at: datetime
    range: str
    window: AnalyticsWindow
    signups: SignupsReport
    activation: ActivationReport
    cohorts: CohortsReport
    engagement: EngagementReport
    content: ContentReport
    notes: List[str]


def ratio(numerator: float, denominator: float) -> Optional[float]:
    """``numerator / denominator`` rounded to 4 places, None on a zero denominator."""
    return round(numerator / denominator, 4) if denominator else None
