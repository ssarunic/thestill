"""Postgres contract suite for the spec #111 Scorecard and connector days.

Cases skip (not fail) when ``TEST_DATABASE_URL`` is unset or unreachable.
NOW is Thursday 2026-10-29 12:00 UTC, so:

- activation signup weeks end with the Monday 10-26 (8 weeks);
- engagement and weekly return activity weeks end with 10-19 (the last
  complete week, 10-19..10-25);
- week-4 retention signup weeks end with 09-21 (five weeks earlier).

Every expected number is derived in the comment next to the reader that
produces it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.integration.test_analytics_repository_contract import PG_DSN, _Seed, pg_only
from thestill.models.analytics import SCORECARD_METRICS, SCORECARD_WEEKS

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
D = timedelta(days=1)


def _utc(month, day, hour=12, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


class _ScorecardSeed(_Seed):
    def reader(self, name, created_at, *, admin=False, analytics=True):
        uid = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO users (id, email, name, created_at, is_admin, usage_analytics_enabled)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (uid, f"{name}@example.com", name, created_at, admin, analytics),
        )
        return (uid, created_at)

    def schedule(self, user, tz, *, enabled=True, updated_at=None):
        self.conn.execute(
            "INSERT INTO user_briefing_schedules (user_id, timezone, enabled, updated_at) VALUES (%s, %s, %s, %s)",
            (user[0], tz, enabled, updated_at or NOW - 60 * D),
        )

    def connector_day(self, user, day):
        self.conn.execute("INSERT INTO user_connector_days (user_id, day) VALUES (%s, %s)", (user[0], day))

    def found(self, user, at):
        """A first open of an episode that isn't in the reader's inbox."""
        self.view(user, self.episode(self.podcast_id, f"found {uuid.uuid4().hex[:6]}", at), at)


@pytest.fixture
def scorecard():
    import psycopg

    from thestill.repositories.postgres_analytics_repository import PostgresAnalyticsRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        s = _ScorecardSeed(conn)

        # Excluded everywhere, though active in every week. Its connector
        # day is still the first one stored (connector_days_since).
        admin = s.reader("admin", _utc(8, 1), admin=True)
        s.follow(admin, _utc(8, 1, 13))
        for day in (14, 15, 20, 21, 22):
            s.read(admin, _utc(10, day))
        s.connector_day(admin, date(2026, 9, 15))

        # --- Activation, signup week of 10-12 (all graded: 10-19 < NOW) ---
        # R1: follow, then an inbox read two days later: activated.
        r1 = s.reader("r1", _utc(10, 12, 10))
        s.follow(r1, _utc(10, 12, 11))
        s.read(r1, _utc(10, 14))
        # R2: follow; an inbox read on the signup day and a found episode
        # later: neither is a delivered return, so not activated.
        r2 = s.reader("r2", _utc(10, 13, 9))
        s.follow(r2, _utc(10, 13, 10))
        s.read(r2, _utc(10, 13, 20))
        s.found(r2, _utc(10, 15))
        # R3: follow, then a connector day two days later: activated.
        r3 = s.reader("r3", _utc(10, 14, 8))
        s.follow(r3, _utc(10, 14, 9))
        s.connector_day(r3, date(2026, 10, 16))
        # R4: follows and never comes back.
        r4 = s.reader("r4", _utc(10, 15))
        s.follow(r4, _utc(10, 15, 13))
        # R5 (Sunday 10-25, week of 10-19): would activate, but only 96 h old.
        r5 = s.reader("r5", _utc(10, 25))
        s.follow(r5, _utc(10, 25, 13))
        s.read(r5, _utc(10, 26))

        # --- Members for the week of 10-19 (signed up by 10-12) -----------
        # All signed up 09-01 (week of 08-31; week 4 = local days 09-29..10-05).
        # M1: active 10-05 (week 4 too); opens on 10-20 and 10-22: engaged.
        m1 = s.reader("m1", _utc(9, 1))
        s.found(m1, _utc(10, 5))
        s.found(m1, _utc(10, 20))
        s.found(m1, _utc(10, 22))
        # M2: active last week (10-14); two reads on one day 10-21: one value
        # day, so not engaged, but returned.
        m2 = s.reader("m2", _utc(9, 1))
        s.read(m2, _utc(10, 14))
        s.read(m2, _utc(10, 21, 10))
        s.read(m2, _utc(10, 21, 18))
        # M3 (Los Angeles): last week 10-15; reads at 23:30 and 00:30 UTC are
        # one local evening (10-21, UTC-7): one day, not engaged; returned.
        m3 = s.reader("m3", _utc(9, 1))
        s.schedule(m3, "America/Los_Angeles")
        s.read(m3, _utc(10, 15))
        s.read(m3, _utc(10, 21, 23, 30))
        s.read(m3, _utc(10, 22, 0, 30))
        # M4: last value day 09-10, more than 28 days before 10-19: lapsed.
        m4 = s.reader("m4", _utc(8, 1))
        s.read(m4, _utc(9, 10))
        # M5: signed up 10-15, so still in its first week: activation's reader.
        m5 = s.reader("m5", _utc(10, 15))
        s.read(m5, _utc(10, 20))
        s.read(m5, _utc(10, 23))
        # M6: opted out, so only inbox reads count: 10-08, then 10-20 and
        # 10-23: engaged (D2: undercounted, never dropped).
        m6 = s.reader("m6", _utc(9, 1), analytics=False)
        for day in (8, 20, 23):
            s.read(m6, _utc(10, day))
        # M7: connector day 10-01 (week 4); a briefing opened 10-20 and a
        # connector day 10-24: engaged.
        m7 = s.reader("m7", _utc(9, 1))
        s.connector_day(m7, date(2026, 10, 1))
        s.briefing(m7, _utc(10, 20, 6), opened_at=_utc(10, 20, 8))
        s.connector_day(m7, date(2026, 10, 24))
        # M8: active last week only (10-13): a member who didn't return. Its
        # briefing was switched off two days ago (a guardrail).
        m8 = s.reader("m8", _utc(9, 1))
        s.read(m8, _utc(10, 13))
        s.schedule(m8, "UTC", enabled=False, updated_at=NOW - 2 * D)
        # M9: switched off long ago: not in the guardrail. A follow alone
        # in the week is setup, not value.
        m9 = s.reader("m9", _utc(9, 1))
        s.schedule(m9, "UTC", enabled=False, updated_at=NOW - 20 * D)
        s.follow(m9, _utc(10, 20))

    repo = PostgresAnalyticsRepository(PG_DSN)
    return repo.scorecard(NOW)


def _metric(report, key):
    return next(m for m in report.metrics if m.key == key)


def _point(report, key, week):
    return next(p for p in _metric(report, key).points if p.week_start == week)


@pg_only
def test_series_shape(scorecard):
    assert [m.key for m in scorecard.metrics] == [d.key for d in SCORECARD_METRICS]
    assert all(len(m.points) == SCORECARD_WEEKS for m in scorecard.metrics)
    assert _metric(scorecard, "activation").points[-1].week_start == date(2026, 10, 26)
    assert _metric(scorecard, "engagement").points[-1].week_start == date(2026, 10, 19)
    assert _metric(scorecard, "weekly_return").points[-1].week_start == date(2026, 10, 19)
    assert _metric(scorecard, "retention_w4").points[-1].week_start == date(2026, 9, 21)


@pg_only
def test_activation_needs_a_follow_and_a_delivered_later_day(scorecard):
    # Graded: R1-R4 and M5 (signed up 10-15, never followed); R1, R3 activated.
    week = _point(scorecard, "activation", date(2026, 10, 12))
    assert (week.eligible, week.reached, week.pct) == (5, 2, 0.4)
    young = _point(scorecard, "activation", date(2026, 10, 19))  # R5: 96 h old, not graded
    assert (young.eligible, young.reached, young.pct) == (0, 0, None)


@pg_only
def test_engagement_counts_value_days_of_members(scorecard):
    week = _point(scorecard, "engagement", date(2026, 10, 19))
    # Members M1 M2 M3 M6 M7 M8 (not M4 lapsed, M5 too new, M9 no value day,
    # R* too new, admin); engaged M1 M6 M7.
    assert (week.eligible, week.reached, week.pct) == (6, 3, 0.5)


@pg_only
def test_weekly_return(scorecard):
    week = _point(scorecard, "weekly_return", date(2026, 10, 19))  # last week M2 M3 M8; returned M2 M3
    assert (week.eligible, week.reached) == (3, 2)


@pg_only
def test_week_four_retention(scorecard):
    week = _point(scorecard, "retention_w4", date(2026, 8, 31))  # M1-M3 M6-M9; M1 (10-05), M7 (10-01)
    assert (week.eligible, week.reached) == (7, 2)


@pg_only
def test_guardrails_and_opt_outs(scorecard):
    guard = {g.key: g for g in scorecard.guardrails}
    assert guard["activated_count"].value == 2  # newest graded signup week: 10-12
    assert guard["briefings_switched_off"].value == 1  # M8, not M9
    assert guard["sign_in_completion"].layer == "B" and guard["sign_in_completion"].value is None
    assert scorecard.opted_out == 1  # M6
    assert scorecard.connector_days_since == date(2026, 9, 15)  # the admin's, any account


# ----- connector days -------------------------------------------------------


@pytest.fixture
def readers():
    import psycopg

    from thestill.repositories.postgres_connector_day_repository import PostgresConnectorDayRepository
    from thestill.repositories.postgres_schema import ensure_schema
    from thestill.repositories.postgres_user_repository import PostgresUserRepository

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        s = _ScorecardSeed(conn)
        la = s.reader("la", _utc(9, 1))
        s.schedule(la, "America/Los_Angeles")
        plain = s.reader("plain", _utc(9, 1))
        off = s.reader("off", _utc(9, 1), analytics=False)
    return PostgresConnectorDayRepository(PG_DSN), PostgresUserRepository(PG_DSN), la[0], plain[0], off[0]


def _days(user_id):
    import psycopg

    with psycopg.connect(PG_DSN) as conn:
        return [
            r[0]
            for r in conn.execute("SELECT day FROM user_connector_days WHERE user_id = %s ORDER BY day", (user_id,))
        ]


@pg_only
def test_connector_day_is_the_readers_local_day(readers):
    days, _, la, plain, _ = readers
    at = _utc(10, 29, 3)  # 20:00 on 10-28 in Los Angeles
    assert days.record_day(la, at) is True
    assert days.record_day(plain, at) is True
    assert days.record_day(la, at + H) is False  # same local day
    assert _days(la) == [date(2026, 10, 28)]
    assert _days(plain) == [date(2026, 10, 29)]


@pg_only
def test_connector_day_follows_the_opt_out(readers):
    days, users, la, _, off = readers
    assert days.record_day(off, NOW) is False
    assert days.record_day("not-a-uuid", NOW) is False
    assert days.record_day(str(uuid.uuid4()), NOW) is False
    days.record_day(la, NOW)
    users.update_usage_analytics(la, False)
    assert _days(la) == [] and _days(off) == []


def test_sqlite_connector_days_store_the_utc_day_and_follow_the_opt_out(tmp_path):
    from thestill.repositories.sqlite_connector_day_repository import SqliteConnectorDayRepository
    from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
    from thestill.repositories.sqlite_user_repository import SqliteUserRepository
    from thestill.utils.sqlite_ext import connect

    db = str(tmp_path / "podcasts.db")
    SqlitePodcastRepository(db)  # creates the schema

    def execute(sql, params=()):
        with connect(db, load_vec="soft") as conn:
            return [tuple(r) for r in conn.execute(sql, params).fetchall()]

    opted_in_id, opted_out_id = str(uuid.uuid4()), str(uuid.uuid4())
    for uid, opted_in in ((opted_in_id, 1), (opted_out_id, 0)):
        execute(
            "INSERT INTO users (id, email, created_at, usage_analytics_enabled) VALUES (?, ?, ?, ?)",
            (uid, f"{uid[:8]}@example.com", NOW.isoformat(), opted_in),
        )
    days = SqliteConnectorDayRepository(db)
    assert days.record_day(opted_in_id, _utc(10, 29, 3)) is True
    assert days.record_day(opted_in_id, _utc(10, 29, 20)) is False
    assert days.record_day(opted_out_id, NOW) is False
    assert days.record_day("nobody", NOW) is False
    assert execute("SELECT user_id, day FROM user_connector_days") == [(opted_in_id, "2026-10-29")]
    SqliteUserRepository(db).update_usage_analytics(opted_in_id, False)
    assert execute("SELECT * FROM user_connector_days") == []
