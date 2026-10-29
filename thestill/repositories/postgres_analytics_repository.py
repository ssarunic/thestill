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

One statement per method (one per table for ``content``); Python only
pivots rows and divides. Two known lower bounds, surfaced on the page as
notes:

- ``user_episode_inbox.state_changed_at`` keeps only the latest change, so
  an episode read and later saved or dismissed no longer counts as read.
- "Active" means a value day (spec #111 §2): a reader-local day with an
  inbox read, a first open, a playback, a briefing opened or a connector
  call (``_VALUE_FACTS``). A follow is setup, not value, so it no longer
  counts; other use leaves no state to count.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Sequence

from structlog import get_logger

from ..models.analytics import (
    ACTIVATION_HOURS,
    BEFORE_TRACKING,
    COHORT_ROWS,
    COHORT_WEEKS,
    ENGAGED_DAYS,
    RETENTION_WEEK,
    SCORECARD_METRICS,
    SCORECARD_WEEKS,
    STEPS,
    UNCATEGORISED,
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
    GuardrailStat,
    MetricPoint,
    MetricSeries,
    PodcastStat,
    ScorecardReport,
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

# Every way a reader reads an episode: an inbox read, a first open
# (``user_episode_views``) or playback (``listened_at``). Opens and playback
# are stored only for readers who allow usage analytics (spec #103 D5), so
# the inbox read keeps everyone else counted. An episode can appear up to
# three times; callers dedupe by (user, episode).
_EPISODE_READS = """
episode_reads AS (
    SELECT i.user_id, i.episode_id, i.state_changed_at AS at
      FROM user_episode_inbox i JOIN real_users ru ON ru.id = i.user_id
     WHERE i.state = 'read' AND i.state_changed_at IS NOT NULL
    UNION ALL
    SELECT v.user_id, v.episode_id, v.first_viewed_at
      FROM user_episode_views v JOIN real_users ru ON ru.id = v.user_id
    UNION ALL
    SELECT p.user_id, p.episode_id, p.listened_at
      FROM user_episode_progress p JOIN real_users ru ON ru.id = p.user_id
     WHERE p.listened_at IS NOT NULL
)"""

# Spec #111 §2: each reader's timezone for bucketing days, from their
# briefing schedule (validated IANA names), UTC without one.
_READER_TZ = """
reader_tz AS (
    SELECT ru.id, ru.created_at, ru.source, COALESCE(s.timezone, 'UTC') AS tz
      FROM real_users ru LEFT JOIN user_briefing_schedules s ON s.user_id = ru.id
)"""

# Every value fact with its moment, its reader-local day, and whether the
# value was delivered (an inbox episode, a briefing or a connector call)
# rather than found. Inbox reads and briefing opens are kept for everyone;
# opens, playback and connector days only for readers who allow usage
# analytics (spec #103 D5, spec #111 D2). A connector day's moment is its
# first call, so activation's deadline compares real times.
_VALUE_FACTS = """
value_facts AS (
    SELECT x.user_id, x.at, (x.at AT TIME ZONE t.tz)::date AS day, x.delivered
      FROM (
            SELECT i.user_id, i.state_changed_at AS at, true AS delivered
              FROM user_episode_inbox i
             WHERE i.state = 'read' AND i.state_changed_at IS NOT NULL
            UNION ALL
            SELECT v.user_id, v.first_viewed_at,
                   EXISTS (SELECT 1 FROM user_episode_inbox i
                            WHERE i.user_id = v.user_id AND i.episode_id = v.episode_id)
              FROM user_episode_views v
            UNION ALL
            SELECT p.user_id, p.listened_at,
                   EXISTS (SELECT 1 FROM user_episode_inbox i
                            WHERE i.user_id = p.user_id AND i.episode_id = p.episode_id)
              FROM user_episode_progress p
             WHERE p.listened_at IS NOT NULL
            UNION ALL
            SELECT b.user_id, b.opened_at, true
              FROM user_briefings b
             WHERE b.opened_at IS NOT NULL
      ) x
      JOIN reader_tz t ON t.id = x.user_id
    UNION ALL
    SELECT c.user_id, c.first_at, c.day, true
      FROM user_connector_days c JOIN reader_tz t ON t.id = c.user_id
)"""

# One row per reader and value day.
_VALUE_DAYS = f"""
{_VALUE_FACTS.strip()},
value_days AS (
    SELECT user_id, day FROM value_facts GROUP BY user_id, day
)"""

# Spec #111 §3.1: the moment a reader became activated, the later of their
# first follow and their first delivered value on a local day after the
# signup day (both needed, in either order). Postgres's greatest() skips
# NULLs, hence the CASE.
_ACTIVATION_FACTS = """
activation_facts AS (
    SELECT t.id AS user_id,
           CASE WHEN ff.at IS NOT NULL AND fr.at IS NOT NULL THEN greatest(ff.at, fr.at) END AS activated_at
      FROM reader_tz t
      LEFT JOIN LATERAL (
            SELECT min(f.created_at) AS at FROM podcast_followers f WHERE f.user_id = t.id
      ) ff ON true
      LEFT JOIN LATERAL (
            SELECT min(v.at) AS at FROM value_facts v
             WHERE v.user_id = t.id AND v.delivered AND v.day > (t.created_at AT TIME ZONE t.tz)::date
      ) fr ON true
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
{_EPISODE_READS.strip()},
{_READER_TZ.strip()},
{_VALUE_FACTS.strip()},
{_ACTIVATION_FACTS.strip()},
cohort AS (
    SELECT ru.id, ru.created_at, ru.source,
           (SELECT min(f.created_at) FROM podcast_followers f WHERE f.user_id = ru.id) AS first_follow_at,
           fb.created_at AS first_briefing_at,
           fb.opened_at AS first_opened_at,
           af.activated_at,
           (SELECT r.at FROM (
                    SELECT min(r.at) AS at FROM episode_reads r
                     WHERE r.user_id = ru.id
                       AND r.at >= ru.created_at
                       AND r.at < ru.created_at + interval '7 days'
                     GROUP BY r.episode_id
             ) r ORDER BY r.at OFFSET 2 LIMIT 1) AS third_read_at
      FROM real_users ru
      LEFT JOIN LATERAL (
            SELECT b.created_at, b.opened_at FROM user_briefings b
             WHERE b.user_id = ru.id ORDER BY b.created_at LIMIT 1
      ) fb ON true
      LEFT JOIN activation_facts af ON af.user_id = ru.id
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

# Week k after signup is local days [signup day + 7k, signup day + 7k + 7),
# graded once the whole week has passed. Also the Scorecard's week-4
# retention series (``weeks = [RETENTION_WEEK]``).
_COHORTS_SQL = f"""
WITH {_REAL_USERS},
{_READER_TZ.strip()},
{_VALUE_DAYS.strip()},
cohort AS (
    SELECT id, created_at, (created_at AT TIME ZONE tz)::date AS signup_day,
           (date_trunc('week', created_at AT TIME ZONE 'UTC'))::date AS week_start
      FROM reader_tz
     WHERE created_at >= %(start)s AND created_at < %(as_of)s
),
cells AS (
    SELECT c.week_start, k.week,
           c.created_at + (k.week + 1) * interval '7 days' <= %(as_of)s AS eligible,
           EXISTS (
               SELECT 1 FROM value_days d
                WHERE d.user_id = c.id
                  AND d.day >= c.signup_day + 7 * k.week
                  AND d.day < c.signup_day + 7 * (k.week + 1)
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
{_EPISODE_READS.strip()},
{_READER_TZ.strip()},
{_VALUE_FACTS.strip()}
SELECT
    (SELECT count(DISTINCT user_id) FROM value_facts
      WHERE at >= %(start)s AND at < %(end)s) AS active_users,
    (SELECT count(DISTINCT (user_id, episode_id)) FROM episode_reads
      WHERE at >= %(start)s AND at < %(end)s) AS reads,
    (SELECT count(*) FROM user_briefings b JOIN real_users ru ON ru.id = b.user_id
      WHERE b.created_at >= %(start)s AND b.created_at < %(end)s) AS briefings_generated,
    (SELECT count(*) FROM user_briefings b JOIN real_users ru ON ru.id = b.user_id
      WHERE b.created_at >= %(start)s AND b.created_at < %(end)s AND b.opened_at IS NOT NULL) AS briefings_opened,
    (SELECT count(*) FROM podcast_followers f JOIN real_users ru ON ru.id = f.user_id
      WHERE f.created_at >= %(start)s AND f.created_at < %(end)s) AS follows
"""

# Spec #111 §3.1: activation per signup week (UTC Monday), maturity-gated.
_SCORECARD_ACTIVATION_SQL = f"""
WITH {_REAL_USERS},
{_READER_TZ.strip()},
{_VALUE_FACTS.strip()},
{_ACTIVATION_FACTS.strip()}
SELECT (date_trunc('week', t.created_at AT TIME ZONE 'UTC'))::date AS week_start,
       count(*) FILTER (WHERE t.created_at <= %(as_of)s - %(hours)s * interval '1 hour') AS eligible,
       count(*) FILTER (WHERE t.created_at <= %(as_of)s - %(hours)s * interval '1 hour'
                          AND a.activated_at <= t.created_at + %(hours)s * interval '1 hour') AS reached
  FROM reader_tz t JOIN activation_facts a ON a.user_id = t.id
 WHERE t.created_at >= %(start)s AND t.created_at < %(as_of)s
 GROUP BY 1
"""

# Spec #111 §3.2 and §3.4, per activity week (Monday, reader-local days).
# A member signed up at least 7 days before the week and had a value day in
# the 28 days before it; engaged = ``engaged_days`` or more value days in the
# week; returned = a value day last week and again this week.
_SCORECARD_WEEKS_SQL = f"""
WITH {_REAL_USERS},
{_READER_TZ.strip()},
{_VALUE_DAYS.strip()},
weeks AS (
    SELECT unnest(%(weeks)s::date[]) AS week_start
),
members AS (
    SELECT w.week_start,
           EXISTS (SELECT 1 FROM value_days d
                    WHERE d.user_id = t.id AND d.day >= w.week_start - 7 AND d.day < w.week_start) AS active_last_week,
           (SELECT count(*) FROM value_days d
             WHERE d.user_id = t.id AND d.day >= w.week_start AND d.day < w.week_start + 7) AS days
      FROM weeks w
      JOIN reader_tz t ON t.created_at <= (w.week_start - 7)::timestamp AT TIME ZONE 'UTC'
     WHERE EXISTS (SELECT 1 FROM value_days d
                    WHERE d.user_id = t.id AND d.day >= w.week_start - 28 AND d.day < w.week_start)
)
SELECT week_start,
       count(*) AS members,
       count(*) FILTER (WHERE days >= %(engaged_days)s) AS engaged,
       count(*) FILTER (WHERE active_last_week) AS active_last_week,
       count(*) FILTER (WHERE active_last_week AND days > 0) AS returned
  FROM members
 GROUP BY week_start
"""

# Spec #111 §3.5 Layer A guardrails and the D2 opted-out count.
_SCORECARD_GUARDRAILS_SQL = f"""
WITH {_REAL_USERS}
SELECT
    (SELECT count(*) FROM users u JOIN real_users ru ON ru.id = u.id
      WHERE NOT u.usage_analytics_enabled) AS opted_out,
    (SELECT count(*) FROM user_briefing_schedules s JOIN real_users ru ON ru.id = s.user_id
      WHERE NOT s.enabled AND s.updated_at >= %(since)s) AS briefings_switched_off,
    (SELECT min(day) FROM user_connector_days) AS connector_days_since
"""

# Spec #103: a reader of an episode opened it (``user_episode_views``,
# the first open) or listened to it (``listened_at``, playback only, never
# a "Mark as played / unplayed"), over the previous window and this one.
# Both are written only for readers who allow usage analytics (D5). An
# old episode reopened counts once, when first opened.
_READS = """
reads AS (
    SELECT v.user_id, v.episode_id, v.first_viewed_at AS at
      FROM user_episode_views v JOIN real_users ru ON ru.id = v.user_id
     WHERE v.first_viewed_at >= %(prev_start)s AND v.first_viewed_at < %(end)s
    UNION ALL
    SELECT p.user_id, p.episode_id, p.listened_at
      FROM user_episode_progress p JOIN real_users ru ON ru.id = p.user_id
     WHERE p.listened_at >= %(prev_start)s AND p.listened_at < %(end)s
)"""

# Listening in this window only; ``completed`` when playback completed the
# row in it (a "Mark as played" sets completed_at but not listened_at).
_LISTENS = """
listens AS (
    SELECT p.user_id, p.episode_id,
           COALESCE(p.completed_at = p.listened_at, false) AS completed
      FROM user_episode_progress p JOIN real_users ru ON ru.id = p.user_id
     WHERE p.listened_at >= %(start)s AND p.listened_at < %(end)s
)"""

# Signed-out public views in the window (spec #103 Phase 2), filed under
# the show: its own page plus its episode pages. Ids are compared as text
# because object_id also holds entity ids, which are not uuids.
_PODCAST_VIEWS = """
podcast_views AS (
    SELECT p.id AS podcast_id, sum(pv.views) AS public_views
      FROM public_page_daily_views pv
      LEFT JOIN episodes e ON pv.kind = 'episode' AND e.id::text = pv.object_id
      JOIN podcasts p ON p.id::text = CASE pv.kind WHEN 'podcast' THEN pv.object_id ELSE e.podcast_id::text END
     WHERE pv.kind IN ('podcast', 'episode') AND pv.day >= %(start_day)s AND pv.day <= %(end_day)s
     GROUP BY p.id
)"""

# The union of the top ``limit`` shows by readers and by followers, so the
# page can sort either way and still show an exact top N. Imported
# episodes' synthetic parents (spec #31) are not shows.
_CONTENT_PODCASTS_SQL = f"""
WITH {_REAL_USERS},
{_READS.strip()},
{_LISTENS.strip()},
{_PODCAST_VIEWS.strip()},
follows AS (
    SELECT f.podcast_id, f.user_id, f.created_at
      FROM podcast_followers f JOIN real_users ru ON ru.id = f.user_id
),
follow_stats AS (
    SELECT podcast_id, count(*) AS followers,
           count(*) FILTER (WHERE created_at >= %(start)s AND created_at < %(end)s) AS new_follows
      FROM follows
     GROUP BY podcast_id
),
read_stats AS (
    SELECT e.podcast_id,
           count(DISTINCT r.user_id) FILTER (WHERE r.at >= %(start)s) AS readers,
           count(DISTINCT r.user_id) FILTER (WHERE r.at < %(start)s) AS readers_previous,
           count(DISTINCT r.user_id) FILTER (WHERE r.at >= %(start)s AND f.user_id IS NOT NULL) AS follower_readers
      FROM reads r
      JOIN episodes e ON e.id = r.episode_id
      LEFT JOIN follows f ON f.podcast_id = e.podcast_id AND f.user_id = r.user_id
     GROUP BY e.podcast_id
),
listen_stats AS (
    SELECT e.podcast_id, count(DISTINCT l.user_id) AS listeners,
           count(DISTINCT l.user_id) FILTER (WHERE l.completed) AS completions
      FROM listens l JOIN episodes e ON e.id = l.episode_id
     GROUP BY e.podcast_id
),
shows AS (
    SELECT p.id, p.slug, p.title, p.primary_category_id,
           COALESCE(fs.followers, 0) AS followers, COALESCE(fs.new_follows, 0) AS new_follows,
           COALESCE(rs.follower_readers, 0) AS follower_readers,
           COALESCE(rs.readers, 0) AS readers, COALESCE(rs.readers_previous, 0) AS readers_previous,
           COALESCE(ls.listeners, 0) AS listeners, COALESCE(ls.completions, 0) AS completions,
           COALESCE(pv.public_views, 0) AS public_views
      FROM podcasts p
      LEFT JOIN follow_stats fs ON fs.podcast_id = p.id
      LEFT JOIN read_stats rs ON rs.podcast_id = p.id
      LEFT JOIN listen_stats ls ON ls.podcast_id = p.id
      LEFT JOIN podcast_views pv ON pv.podcast_id = p.id
     WHERE NOT p.synthetic AND (fs.podcast_id IS NOT NULL OR rs.podcast_id IS NOT NULL)
),
ranked AS (
    SELECT s.*,
           row_number() OVER (ORDER BY readers DESC, followers DESC, title, id) AS by_readers,
           row_number() OVER (ORDER BY followers DESC, readers DESC, title, id) AS by_followers
      FROM shows s
)
SELECT r.*, top.name AS category
  FROM ranked r
  LEFT JOIN categories c ON c.id = r.primary_category_id
  LEFT JOIN categories top ON top.id = COALESCE(c.parent_id, c.id)
 WHERE r.by_readers <= %(limit)s OR r.by_followers <= %(limit)s
 ORDER BY r.by_readers
"""

_CONTENT_EPISODES_SQL = f"""
WITH {_REAL_USERS},
{_READS.strip()},
{_LISTENS.strip()},
read_stats AS (
    SELECT episode_id, count(DISTINCT user_id) AS readers
      FROM reads
     WHERE at >= %(start)s
     GROUP BY episode_id
),
listen_stats AS (
    SELECT episode_id, count(DISTINCT user_id) AS listeners,
           count(DISTINCT user_id) FILTER (WHERE completed) AS completions
      FROM listens
     GROUP BY episode_id
),
episode_views AS (
    SELECT object_id, sum(views) AS public_views
      FROM public_page_daily_views
     WHERE kind = 'episode' AND day >= %(start_day)s AND day <= %(end_day)s
     GROUP BY object_id
)
SELECT e.id, e.slug, e.title, p.slug AS podcast_slug, p.title AS podcast_title,
       COALESCE(e.pub_date, e.published_at) AS published_at, p.synthetic AS imported,
       rs.readers, COALESCE(ls.listeners, 0) AS listeners, COALESCE(ls.completions, 0) AS completions,
       COALESCE(ev.public_views, 0) AS public_views
  FROM read_stats rs
  JOIN episodes e ON e.id = rs.episode_id
  JOIN podcasts p ON p.id = e.podcast_id
  LEFT JOIN listen_stats ls ON ls.episode_id = rs.episode_id
  LEFT JOIN episode_views ev ON ev.object_id = rs.episode_id::text
 ORDER BY rs.readers DESC, listeners DESC, COALESCE(e.pub_date, e.published_at) DESC NULLS LAST, e.id
 LIMIT %(limit)s
"""

# Each show counts once, under the top level of its primary category
# (decision D2); 0 stands for "no category" so it joins like any other key.
# A category is listed for follows or reading activity in either window.
_CONTENT_CATEGORIES_SQL = f"""
WITH {_REAL_USERS},
{_READS.strip()},
{_PODCAST_VIEWS.strip()},
shows AS (
    SELECT p.id, COALESCE(c.parent_id, c.id, 0) AS top_id
      FROM podcasts p LEFT JOIN categories c ON c.id = p.primary_category_id
     WHERE NOT p.synthetic
),
follow_stats AS (
    SELECT s.top_id, count(DISTINCT f.podcast_id) AS shows, count(DISTINCT f.user_id) AS followers
      FROM podcast_followers f
      JOIN real_users ru ON ru.id = f.user_id
      JOIN shows s ON s.id = f.podcast_id
     GROUP BY s.top_id
),
read_stats AS (
    SELECT s.top_id,
           count(DISTINCT e.podcast_id) FILTER (WHERE r.at >= %(start)s) AS shows_read,
           count(DISTINCT r.user_id) FILTER (WHERE r.at >= %(start)s) AS readers,
           count(DISTINCT r.user_id) FILTER (WHERE r.at < %(start)s) AS readers_previous
      FROM reads r
      JOIN episodes e ON e.id = r.episode_id
      JOIN shows s ON s.id = e.podcast_id
     GROUP BY s.top_id
),
view_stats AS (
    SELECT s.top_id, sum(pv.public_views) AS public_views
      FROM podcast_views pv JOIN shows s ON s.id = pv.podcast_id
     GROUP BY s.top_id
)
SELECT COALESCE(top.name, %(uncategorised)s) AS category,
       COALESCE(fs.shows, 0) AS shows, COALESCE(rs.shows_read, 0) AS shows_read,
       COALESCE(fs.followers, 0) AS followers,
       COALESCE(rs.readers, 0) AS readers, COALESCE(rs.readers_previous, 0) AS readers_previous,
       COALESCE(vs.public_views, 0) AS public_views
  FROM follow_stats fs
  FULL JOIN read_stats rs ON rs.top_id = fs.top_id
  LEFT JOIN view_stats vs ON vs.top_id = COALESCE(fs.top_id, rs.top_id)
  LEFT JOIN categories top ON top.id = COALESCE(fs.top_id, rs.top_id)
 ORDER BY readers DESC, followers DESC, category
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

    def scorecard(self, as_of: datetime) -> ScorecardReport:
        this_monday = _monday(as_of.astimezone(timezone.utc).date())

        def weeks_ending(newest: date) -> List[date]:
            return [newest - timedelta(weeks=i) for i in range(SCORECARD_WEEKS - 1, -1, -1)]

        def midnight(day: date) -> datetime:
            return datetime.combine(day, time.min, tzinfo=timezone.utc)

        # Activation: signup weeks up to this one (the newest are part-graded).
        # Week-4 retention: signup weeks old enough to have reached week 4.
        # Engagement and return: the last complete activity weeks.
        signup_weeks = weeks_ending(this_monday)
        retention_weeks = weeks_ending(this_monday - timedelta(weeks=RETENTION_WEEK + 1))
        activity_weeks = weeks_ending(this_monday - timedelta(weeks=1))

        activation = {
            row["week_start"]: (_n(row, "eligible"), _n(row, "reached"))
            for row in self._rows(
                _SCORECARD_ACTIVATION_SQL, start=midnight(signup_weeks[0]), as_of=as_of, hours=ACTIVATION_HOURS
            )
        }
        retention = {
            row["week_start"]: (_n(row, "eligible"), _n(row, "active"))
            for row in self._rows(_COHORTS_SQL, start=midnight(retention_weeks[0]), as_of=as_of, weeks=[RETENTION_WEEK])
        }
        engagement: Dict[date, tuple] = {}
        weekly_return: Dict[date, tuple] = {}
        for row in self._rows(_SCORECARD_WEEKS_SQL, weeks=activity_weeks, engaged_days=ENGAGED_DAYS):
            engagement[row["week_start"]] = (_n(row, "members"), _n(row, "engaged"))
            weekly_return[row["week_start"]] = (_n(row, "active_last_week"), _n(row, "returned"))

        def series(definition, weeks: List[date], counts: Dict[date, tuple]) -> MetricSeries:
            points = []
            for week in weeks:
                eligible, reached = counts.get(week, (0, 0))
                points.append(
                    MetricPoint(week_start=week, eligible=eligible, reached=reached, pct=ratio(reached, eligible))
                )
            return MetricSeries(
                key=definition.key,
                label=definition.label,
                readers=definition.readers,
                definition=definition.definition,
                points=points,
            )

        weeks_and_counts = {
            "activation": (signup_weeks, activation),
            "engagement": (activity_weeks, engagement),
            "retention_w4": (retention_weeks, retention),
            "weekly_return": (activity_weeks, weekly_return),
        }
        metrics = [series(d, *weeks_and_counts[d.key]) for d in SCORECARD_METRICS]

        guard = self._rows(_SCORECARD_GUARDRAILS_SQL, since=as_of - timedelta(days=7))[0]
        graded = [p for p in metrics[0].points if p.eligible]
        latest = graded[-1] if graded else None
        guardrails = [
            GuardrailStat(
                key="activated_count",
                label="Activated readers",
                layer="A",
                value=latest.reached if latest else None,
                detail=(
                    f"in the newest graded signup week ({latest.week_start:%Y-%m-%d})"
                    if latest
                    else "no signup week graded yet"
                ),
            ),
            GuardrailStat(
                key="briefings_switched_off",
                label="Briefings switched off",
                layer="A",
                value=_n(guard, "briefings_switched_off"),
                detail="schedules off and last edited in the past 7 days",
            ),
            GuardrailStat(
                key="sign_in_completion",
                label="Sign-in completion",
                layer="B",
                value=None,
                detail="PostHog: login_started → login_completed",
            ),
            GuardrailStat(
                key="early_unfollows",
                label="Follows undone within 7 days",
                layer="B",
                value=None,
                detail="PostHog: follow then unfollow by the same reader",
            ),
        ]
        return ScorecardReport(
            metrics=metrics,
            guardrails=guardrails,
            opted_out=_n(guard, "opted_out"),
            connector_days_since=guard.get("connector_days_since"),
        )

    def content(self, window: AnalyticsWindow, previous: AnalyticsWindow, *, limit: int) -> ContentReport:
        # The SQL names every column after its model field and coalesces
        # every count, so rows validate as they are.
        params = {
            "start": window.start,
            "end": window.end,
            "prev_start": previous.start,
            "limit": limit,
            # public_page_daily_views is per UTC day; the window's last day is
            # the one ``end`` falls in.
            "start_day": window.start.astimezone(timezone.utc).date(),
            "end_day": (window.end - timedelta(microseconds=1)).astimezone(timezone.utc).date(),
        }
        return ContentReport(
            podcasts=[
                PodcastStat.model_validate({**row, "podcast_id": str(row["id"])})
                for row in self._rows(_CONTENT_PODCASTS_SQL, **params)
            ],
            episodes=[
                EpisodeStat.model_validate({**row, "episode_id": str(row["id"])})
                for row in self._rows(_CONTENT_EPISODES_SQL, **params)
            ],
            categories=[
                CategoryStat.model_validate(row)
                for row in self._rows(_CONTENT_CATEGORIES_SQL, uncategorised=UNCATEGORISED, **params)
            ],
        )
