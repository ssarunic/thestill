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

"""PostgreSQL implementation of ``AnalyticsRepository`` (spec #95 Layer A).

One statement per method; Python only pivots rows and divides. Two known
lower bounds, surfaced on the page as notes:

- ``user_episode_inbox.state_changed_at`` keeps only the latest change, so
  an episode read and later saved or dismissed no longer counts as read.
- "Active" means a read, a follow or a briefing opened; other use leaves
  no state to count.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Sequence

from structlog import get_logger

from ..models.analytics import (
    BEFORE_TRACKING,
    COHORT_ROWS,
    COHORT_WEEKS,
    STEPS,
    ActivationGroup,
    ActivationReport,
    AnalyticsWindow,
    CohortCell,
    CohortRow,
    CohortsReport,
    EngagementReport,
    SignupDay,
    SignupsReport,
    StepStat,
    ratio,
)
from ..utils.postgres_ext import connect
from .analytics_repository import AnalyticsRepository

logger = get_logger(__name__)

# The population every query counts: real people, never admins or the
# single-user local account.
_REAL_USERS = f"""
real_users AS (
    SELECT id, created_at, COALESCE(signup_source, '{BEFORE_TRACKING}') AS source
      FROM users
     WHERE NOT is_admin AND email <> ALL(%(excluded)s::text[])
)"""

# Activity that counts towards retention and active users.
_EVENTS = """
events AS (
    SELECT i.user_id, i.state_changed_at AS at
      FROM user_episode_inbox i JOIN real_users ru ON ru.id = i.user_id
     WHERE i.state = 'read' AND i.state_changed_at IS NOT NULL
    UNION ALL
    SELECT f.user_id, f.created_at FROM podcast_followers f JOIN real_users ru ON ru.id = f.user_id
    UNION ALL
    SELECT b.user_id, b.opened_at
      FROM user_briefings b JOIN real_users ru ON ru.id = b.user_id
     WHERE b.opened_at IS NOT NULL
)"""

# One VALUES row per step; ``c.<fact>_at`` is the cohort CTE's column.
_STEP_VALUES = ",\n".join(f"('{s.key}', interval '{s.horizon_hours} hours', c.{s.fact}_at)" for s in STEPS)

_SIGNUPS_SQL = f"""
WITH {_REAL_USERS}
SELECT (created_at AT TIME ZONE 'UTC')::date AS day, source, count(*) AS n
  FROM real_users
 WHERE created_at >= %(start)s AND created_at < %(end)s
 GROUP BY 1, 2
"""

# A follow inherited from the legacy local account (spec #64) predates the
# signup; GREATEST clamps it to "at signup" rather than a negative delay.
_ACTIVATION_SQL = f"""
WITH {_REAL_USERS},
cohort AS (
    SELECT ru.id, ru.created_at, ru.source,
           (SELECT min(f.created_at) FROM podcast_followers f WHERE f.user_id = ru.id) AS first_follow_at,
           fb.created_at AS first_briefing_at,
           fb.opened_at AS first_opened_at,
           (SELECT i.state_changed_at FROM user_episode_inbox i
             WHERE i.user_id = ru.id AND i.state = 'read'
               AND i.state_changed_at >= ru.created_at
               AND i.state_changed_at < ru.created_at + interval '7 days'
             ORDER BY i.state_changed_at OFFSET 2 LIMIT 1) AS third_read_at
      FROM real_users ru
      LEFT JOIN LATERAL (
            SELECT b.created_at, b.opened_at FROM user_briefings b
             WHERE b.user_id = ru.id ORDER BY b.created_at LIMIT 1
      ) fb ON true
     WHERE ru.created_at >= %(start)s AND ru.created_at < %(end)s
),
steps AS (
    SELECT c.source, c.created_at, s.key, s.horizon,
           CASE WHEN s.at IS NULL THEN NULL ELSE greatest(s.at, c.created_at) END AS at
      FROM cohort c
     CROSS JOIN LATERAL (VALUES
{_STEP_VALUES}
     ) AS s(key, horizon, at)
),
graded AS (
    SELECT source, key, created_at <= %(end)s - horizon AS eligible,
           created_at <= %(end)s - horizon AND at IS NOT NULL AND at <= created_at + horizon AS reached,
           extract(epoch FROM at - created_at) / 3600.0 AS hours
      FROM steps
)
SELECT key, grouping(source) = 1 AS overall, source,
       count(*) AS signups,
       count(*) FILTER (WHERE eligible) AS eligible,
       count(*) FILTER (WHERE reached) AS reached,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY hours) FILTER (WHERE reached) AS median_hours
  FROM graded
 GROUP BY GROUPING SETS ((key), (key, source))
"""

_COHORTS_SQL = f"""
WITH {_REAL_USERS},
{_EVENTS.strip()},
cohort AS (
    SELECT id, created_at, (date_trunc('week', created_at AT TIME ZONE 'UTC'))::date AS week_start
      FROM real_users
     WHERE created_at >= %(start)s AND created_at < %(as_of)s
),
cells AS (
    SELECT c.week_start, k.week,
           c.created_at + (k.week + 1) * interval '7 days' <= %(as_of)s AS eligible,
           EXISTS (
               SELECT 1 FROM events e
                WHERE e.user_id = c.id
                  AND e.at >= c.created_at + k.week * interval '7 days'
                  AND e.at < c.created_at + (k.week + 1) * interval '7 days'
           ) AS active
      FROM cohort c
     CROSS JOIN unnest(%(weeks)s::int[]) AS k(week)
)
SELECT week_start, week,
       count(*) AS size,
       count(*) FILTER (WHERE eligible) AS eligible,
       count(*) FILTER (WHERE eligible AND active) AS active
  FROM cells
 GROUP BY week_start, week
"""

_ENGAGEMENT_SQL = f"""
WITH {_REAL_USERS},
{_EVENTS.strip()}
SELECT
    (SELECT count(DISTINCT user_id) FROM events
      WHERE at >= %(start)s AND at < %(end)s) AS active_users,
    (SELECT count(*) FROM user_episode_inbox i JOIN real_users ru ON ru.id = i.user_id
      WHERE i.state = 'read' AND i.state_changed_at >= %(start)s AND i.state_changed_at < %(end)s) AS reads,
    (SELECT count(*) FROM user_briefings b JOIN real_users ru ON ru.id = b.user_id
      WHERE b.created_at >= %(start)s AND b.created_at < %(end)s) AS briefings_generated,
    (SELECT count(*) FROM user_briefings b JOIN real_users ru ON ru.id = b.user_id
      WHERE b.created_at >= %(start)s AND b.created_at < %(end)s AND b.opened_at IS NOT NULL) AS briefings_opened,
    (SELECT count(*) FROM podcast_followers f JOIN real_users ru ON ru.id = f.user_id
      WHERE f.created_at >= %(start)s AND f.created_at < %(end)s) AS follows
"""


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _n(row: dict, column: str) -> int:
    """An integer column of ``row``; 0 when the row or value is missing."""
    return int(row.get(column) or 0)


class PostgresAnalyticsRepository(AnalyticsRepository):
    """PostgreSQL-backed Layer A analytics."""

    def __init__(self, dsn: str, *, exclude_emails: Sequence[str] = ()):
        self.dsn = dsn
        self._excluded: List[str] = list(exclude_emails)
        logger.info("Initialized Postgres analytics repository")

    def _rows(self, sql: str, **params) -> List[dict]:
        with connect(self.dsn) as conn:
            return conn.execute(sql, {"excluded": self._excluded, **params}).fetchall()

    def signups(self, window: AnalyticsWindow) -> SignupsReport:
        per_day: Dict[date, Dict[str, int]] = defaultdict(dict)
        totals: Counter = Counter()
        for row in self._rows(_SIGNUPS_SQL, start=window.start, end=window.end):
            per_day[row["day"]][row["source"]] = _n(row, "n")
            totals[row["source"]] += _n(row, "n")
        first = window.start.astimezone(timezone.utc).date()
        last = (window.end - timedelta(microseconds=1)).astimezone(timezone.utc).date()
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        return SignupsReport(
            total=sum(totals.values()),
            by_source=dict(totals),
            days=[SignupDay(day=d, total=sum(per_day[d].values()), by_source=per_day[d]) for d in days],
        )

    def activation(self, window: AnalyticsWindow) -> ActivationReport:
        rows: Dict[str, Dict[str, dict]] = defaultdict(dict)  # group -> step key -> row
        signups: Dict[str, int] = {}
        for row in self._rows(_ACTIVATION_SQL, start=window.start, end=window.end):
            source = "all" if row["overall"] else row["source"]
            rows[source][row["key"]] = row
            signups[source] = _n(row, "signups")  # the same on every step row of a group

        def step(source: str, definition) -> StepStat:
            row = rows[source].get(definition.key, {})
            eligible, reached, median = _n(row, "eligible"), _n(row, "reached"), row.get("median_hours")
            return StepStat(
                key=definition.key,
                label=definition.label,
                horizon_hours=definition.horizon_hours,
                eligible=eligible,
                reached=reached,
                pct=ratio(reached, eligible),
                median_hours=round(float(median), 1) if median is not None else None,
            )

        def group(source: str) -> ActivationGroup:
            return ActivationGroup(
                source=source, signups=signups.get(source, 0), steps=[step(source, d) for d in STEPS]
            )

        by_source = sorted((s for s in signups if s != "all"), key=lambda s: (-signups[s], s))
        return ActivationReport(overall=group("all"), by_source=[group(s) for s in by_source])

    def cohorts(self, as_of: datetime) -> CohortsReport:
        newest = _monday(as_of.astimezone(timezone.utc).date())
        week_starts = [newest - timedelta(weeks=i) for i in range(COHORT_ROWS - 1, -1, -1)]
        start = datetime.combine(week_starts[0], time.min, tzinfo=timezone.utc)
        cells: Dict[date, Dict[int, dict]] = defaultdict(dict)  # week start -> activity week -> row
        sizes: Dict[date, int] = {}
        for row in self._rows(_COHORTS_SQL, start=start, as_of=as_of, weeks=list(COHORT_WEEKS)):
            cells[row["week_start"]][int(row["week"])] = row
            sizes[row["week_start"]] = _n(row, "size")  # the same on every week of a cohort

        def cell(week_start: date, week: int) -> CohortCell:
            row = cells[week_start].get(week, {})
            eligible, active = _n(row, "eligible"), _n(row, "active")
            return CohortCell(week=week, eligible=eligible, active=active, pct=ratio(active, eligible))

        return CohortsReport(
            weeks=list(COHORT_WEEKS),
            rows=[
                CohortRow(week_start=ws, size=sizes.get(ws, 0), cells=[cell(ws, w) for w in COHORT_WEEKS])
                for ws in week_starts
            ],
        )

    def engagement(self, window: AnalyticsWindow) -> EngagementReport:
        row = self._rows(_ENGAGEMENT_SQL, start=window.start, end=window.end)[0]
        active, reads, follows = _n(row, "active_users"), _n(row, "reads"), _n(row, "follows")
        generated, opened = _n(row, "briefings_generated"), _n(row, "briefings_opened")
        # The window ends "now", part-way through today: divide by the time
        # that has actually elapsed, not by whole days.
        weeks = (window.end - window.start) / timedelta(weeks=1)
        return EngagementReport(
            active_users=active,
            reads=reads,
            reads_per_active_user_per_week=ratio(reads, active * weeks),
            briefings_generated=generated,
            briefings_opened=opened,
            opened_ratio=ratio(opened, generated),
            follows=follows,
            follows_per_active_user=ratio(follows, active),
        )
