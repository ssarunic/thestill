"""Postgres contract suite for Layer A product analytics (spec #95 Phase 0).

Postgres only: SQLite installs get ``UnavailableAnalyticsRepository``.
Cases skip (not fail) when ``TEST_DATABASE_URL`` is unset or unreachable.

The seed is small and hand-computable; every expected number below is
derived in the comment next to the user that produces it.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from thestill.models.analytics import BEFORE_TRACKING, COHORT_ROWS, UNCATEGORISED
from thestill.repositories.analytics_repository import AnalyticsUnavailableError, UnavailableAnalyticsRepository
from thestill.services.analytics_service import AnalyticsService

PG_DSN = os.getenv("TEST_DATABASE_URL", "")


def _pg_reachable(dsn: str) -> bool:
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


PG_OK = _pg_reachable(PG_DSN)
pg_only = pytest.mark.skipif(not PG_OK, reason="Postgres not reachable — set TEST_DATABASE_URL")

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
LOCAL_EMAIL = "local@thestill.me"
H = timedelta(hours=1)
D = timedelta(days=1)


class _Seed:
    """Raw inserts with explicit timestamps (the funnel needs exact offsets)."""

    def __init__(self, conn):
        self.conn = conn
        self.podcast_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, image_url) VALUES (%s, %s, %s, %s, %s)",
            (self.podcast_id, "https://example.com/a.xml", "Podcast A", "podcast-a", ""),
        )
        self.podcast_ids = [self.podcast_id]
        self._episodes = 0

    def user(self, name, created_at, *, source=None, admin=False, email=None):
        uid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO users (id, email, name, created_at, is_admin, signup_source) VALUES (%s, %s, %s, %s, %s, %s)",
            (uid, email or f"{name}@example.com", name, created_at, admin, source),
        )
        return (uid, created_at)

    def follow(self, user, at):
        # One follow per (user, podcast): mint a podcast per follow.
        pid = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO podcasts (id, rss_url, title, slug, image_url) VALUES (%s, %s, %s, %s, '')",
            (pid, f"https://example.com/{pid}.xml", f"P {pid}", pid),
        )
        self.conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), user[0], pid, at),
        )

    def read(self, user, at):
        self._episodes += 1
        eid = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO episodes (id, podcast_id, external_id, title, slug, description,
                                  description_html, audio_url, published_at, pub_date)
            VALUES (%s, %s, %s, %s, %s, '', '', 'https://example.com/a.mp3', %s, %s)
            """,
            (eid, self.podcast_id, f"ext-{self._episodes}", f"E{self._episodes}", f"e-{self._episodes}", at, at),
        )
        self.conn.execute(
            """
            INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
            VALUES (%s, %s, %s, 'follow_new', 'read', %s, %s)
            """,
            (str(uuid.uuid4()), user[0], eid, at - H, at),
        )

    # Spec #103 content helpers: shared shows and episodes, so several
    # users can follow, read and listen to the same thing.
    def category(self, name, parent=None):
        return self.conn.execute(
            "INSERT INTO categories (name, slug, parent_id) VALUES (%s, %s, %s) RETURNING id",
            (name, name.lower().replace(" ", "-"), parent),
        ).fetchone()[0]

    def show(self, title, *, category=None, synthetic=False):
        pid = str(uuid.uuid4())
        slug = title.lower().replace(" ", "-")
        self.conn.execute(
            """
            INSERT INTO podcasts (id, rss_url, title, slug, image_url, primary_category_id, synthetic)
            VALUES (%s, %s, %s, %s, '', %s, %s)
            """,
            (pid, f"https://example.com/{pid}.xml", title, slug, category, synthetic),
        )
        return pid

    def episode(self, podcast_id, title, published):
        self._episodes += 1
        eid = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT INTO episodes (id, podcast_id, external_id, title, slug, description,
                                  description_html, audio_url, published_at, pub_date)
            VALUES (%s, %s, %s, %s, %s, '', '', 'https://example.com/a.mp3', %s, %s)
            """,
            (eid, podcast_id, f"ext-{self._episodes}", title, title.lower(), published, published),
        )
        return eid

    def follow_show(self, user, podcast_id, at):
        self.conn.execute(
            "INSERT INTO podcast_followers (id, user_id, podcast_id, created_at) VALUES (%s, %s, %s, %s)",
            (str(uuid.uuid4()), user[0], podcast_id, at),
        )

    def read_episode(self, user, episode_id, at):
        self.conn.execute(
            """
            INSERT INTO user_episode_inbox (id, user_id, episode_id, source, state, delivered_at, state_changed_at)
            VALUES (%s, %s, %s, 'follow_new', 'read', %s, %s)
            """,
            (str(uuid.uuid4()), user[0], episode_id, at - H, at),
        )

    def progress(self, user, episode_id, at, *, position, completed_at=None):
        self.conn.execute(
            """
            INSERT INTO user_episode_progress (user_id, episode_id, position_seconds, session_id, seq,
                                               updated_at, completed_at)
            VALUES (%s, %s, %s, 's', 1, %s, %s)
            """,
            (user[0], episode_id, position, at, completed_at),
        )

    def briefing(self, user, created_at, opened_at=None):
        self.conn.execute(
            """
            INSERT INTO user_briefings (id, user_id, cursor_from, cursor_to, episode_count, created_at, opened_at)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
            """,
            (str(uuid.uuid4()), user[0], created_at - D, created_at, created_at, opened_at),
        )


@pytest.fixture
def service():
    import psycopg

    from thestill.repositories.postgres_analytics_repository import PostgresAnalyticsRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        s = _Seed(conn)

        # Excluded everywhere, though active inside every window.
        admin = s.user("admin", NOW - 10 * D, source="direct", admin=True)
        s.follow(admin, NOW - 10 * D + H)
        s.read(admin, NOW - 5 * D)
        local = s.user("local", NOW - 10 * D, email=LOCAL_EMAIL)
        s.follow(local, NOW - 10 * D + H)

        # A (episode, -20d): follow +2h; briefing +24h opened +26h; reads
        # +1d/+2d/+3d (3rd at 72 h) and +8d (activity week 1).
        a = s.user("a", NOW - 20 * D, source="episode")
        s.follow(a, a[1] + 2 * H)
        s.briefing(a, a[1] + 24 * H, opened_at=a[1] + 26 * H)
        for day in (1, 2, 3, 8):
            s.read(a, a[1] + day * D)
        # B (direct, -10d): follow +30h; briefing +48h never opened; 2 reads.
        b = s.user("b", NOW - 10 * D, source="direct")
        s.follow(b, b[1] + 30 * H)
        s.briefing(b, b[1] + 48 * H)
        s.read(b, b[1] + 1 * D)
        s.read(b, b[1] + 2 * D)
        # C (no source = before tracking, -9d): did nothing.
        s.user("c", NOW - 9 * D)
        # D (episode, -3d): follow +1h. Old enough for 24 h, not for 7 d.
        d = s.user("d", NOW - 3 * D, source="episode")
        s.follow(d, d[1] + 1 * H)
        # E (top, -12h): too young for any step.
        s.user("e", NOW - 12 * H, source="top")
        # F (podcast, -40d): outside the 28d window; retention only.
        # Follow +1h (week 0), read +30d (week 4, inside the 28d window).
        f = s.user("f", NOW - 40 * D, source="podcast")
        s.follow(f, f[1] + 1 * H)
        s.read(f, f[1] + 30 * D)

    repo = PostgresAnalyticsRepository(PG_DSN, exclude_emails=(LOCAL_EMAIL,))
    return AnalyticsService(repo, enabled=True, clock=lambda: NOW)


def _step(group, key):
    return next(s for s in group.steps if s.key == key)


@pg_only
def test_signups_split_by_source_and_zero_filled(service):
    report = service.report("28d")
    assert report.signups.total == 5  # A B C D E; F is older, admin/local excluded
    assert report.signups.by_source == {"episode": 2, "direct": 1, BEFORE_TRACKING: 1, "top": 1}
    assert len(report.signups.days) == 28
    assert report.signups.days[0].day == date(2026, 9, 17)
    assert report.signups.days[-1].day == date(2026, 10, 14)
    by_day = {d.day: d for d in report.signups.days}
    assert by_day[(NOW - 20 * D).date()].by_source == {"episode": 1}
    assert by_day[date(2026, 9, 20)].total == 0


@pg_only
def test_activation_overall_is_maturity_gated(service):
    overall = service.report("28d").activation.overall
    assert overall.source == "all" and overall.signups == 5

    assert [s.label for s in overall.steps][0] == "Followed a show within 24 h"
    follow_24h = _step(overall, "follow_24h")  # eligible A B C D; reached A (2h) D (1h)
    assert (follow_24h.eligible, follow_24h.reached, follow_24h.pct, follow_24h.median_hours) == (4, 2, 0.5, 1.5)

    follow_7d = _step(overall, "follow_7d")  # eligible A B C; reached A (2h) B (30h)
    assert (follow_7d.eligible, follow_7d.reached, follow_7d.median_hours) == (3, 2, 16.0)
    assert follow_7d.pct == pytest.approx(0.6667)

    generated = _step(overall, "briefing_generated")  # A 24h, B 48h
    assert (generated.eligible, generated.reached, generated.median_hours) == (3, 2, 36.0)

    opened = _step(overall, "briefing_opened")  # A 26h only
    assert (opened.eligible, opened.reached, opened.median_hours) == (3, 1, 26.0)

    reads = _step(overall, "reads_3_first_week")  # A's 3rd read at 72h; B has 2
    assert (reads.eligible, reads.reached, reads.median_hours) == (3, 1, 72.0)


@pg_only
def test_activation_per_source(service):
    groups = {g.source: g for g in service.report("28d").activation.by_source}
    assert [g.source for g in service.report("28d").activation.by_source] == [
        "episode",
        BEFORE_TRACKING,
        "direct",
        "top",
    ]
    episode = groups["episode"]  # A and D
    assert episode.signups == 2
    assert (_step(episode, "follow_24h").eligible, _step(episode, "follow_24h").reached) == (2, 2)
    assert (_step(episode, "follow_7d").eligible, _step(episode, "follow_7d").reached) == (1, 1)
    top = groups["top"]  # E: too young for anything
    assert all(s.eligible == 0 and s.pct is None and s.median_hours is None for s in top.steps)


@pg_only
def test_cohorts_fixed_twelve_weeks_with_signup_relative_weeks(service):
    cohorts = service.report("7d").cohorts  # independent of the range
    assert cohorts.weeks == [1, 2, 4, 8]
    assert len(cohorts.rows) == COHORT_ROWS
    assert cohorts.rows[-1].week_start == date(2026, 10, 12)  # Monday of NOW
    assert all(r.week_start.weekday() == 0 for r in cohorts.rows)
    rows = {r.week_start: r for r in cohorts.rows}

    def monday(dt):
        day = dt.date()
        return day - timedelta(days=day.weekday())

    # F: active in week 4 but not 1 or 2; week 8 not yet eligible.
    f_row = rows[monday(NOW - 40 * D)]
    f_cells = {c.week: c for c in f_row.cells}
    assert f_row.size == 1
    assert (f_cells[1].eligible, f_cells[1].active) == (1, 0)
    assert (f_cells[2].eligible, f_cells[2].active) == (1, 0)
    assert (f_cells[4].eligible, f_cells[4].active, f_cells[4].pct) == (1, 1, 1.0)
    assert (f_cells[8].eligible, f_cells[8].pct) == (0, None)

    # A: read at +8d is week 1; week 2 has not fully passed yet.
    a_cells = {c.week: c for c in rows[monday(NOW - 20 * D)].cells}
    assert (a_cells[1].eligible, a_cells[1].active) == (1, 1)
    assert a_cells[2].eligible == 0

    # Empty weeks are present, zero-sized.
    assert sum(r.size for r in cohorts.rows) == 6  # A-F; admin/local excluded


@pg_only
def test_engagement(service):
    report = service.report("28d")
    e = report.engagement
    assert e.active_users == 4  # A B D F (C, E idle; admin/local excluded)
    assert e.reads == 7  # A 4 + B 2 + F 1
    weeks = (report.window.end - report.window.start) / timedelta(weeks=1)  # 27.5 days so far
    assert e.reads_per_active_user_per_week == pytest.approx(7 / (4 * weeks), abs=1e-4)
    assert (e.briefings_generated, e.briefings_opened, e.opened_ratio) == (2, 1, 0.5)
    assert e.follows == 3  # A B D (F's follow predates the window)
    assert e.follows_per_active_user == 0.75


# ----- spec #103 content popularity ---------------------------------------
#
# 28d window [09-17 00:00, NOW); previous window of the same 27.5 days
# [08-20 12:00, 09-17 00:00). Category names carry a per-run tag because
# the fixture leaves the categories table alone.


@pytest.fixture
def content():
    import psycopg

    from thestill.repositories.postgres_analytics_repository import PostgresAnalyticsRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes CASCADE")
        s = _Seed(conn)
        tech = s.category(f"Technology {tag}")
        tech_news = s.category("Tech News", parent=tech)
        comedy = s.category(f"Comedy {tag}")
        history = s.category(f"History {tag}")  # one idle show: no row
        news = s.category(f"News {tag}")  # followed, never read

        alpha = s.show("Alpha", category=tech)
        beta = s.show("Beta", category=tech_news)  # rolls up to Technology
        gamma = s.show("Gamma")  # uncategorised
        delta = s.show("Delta", category=comedy)  # nobody follows it
        s.show("Zeta", category=history)
        epsilon = s.show("Epsilon", category=news)
        imports = s.show("Imports", synthetic=True)

        a1 = s.episode(alpha, "A1", NOW - 6 * D)
        a2 = s.episode(alpha, "A2", NOW - 31 * D)
        b1 = s.episode(beta, "B1", NOW - 36 * D)
        g1 = s.episode(gamma, "G1", NOW - 10 * D)
        d1 = s.episode(delta, "D1", NOW - 20 * D)
        i1 = s.episode(imports, "I1", NOW - 2 * D)

        # The admin follows and reads everything; never counted.
        admin = s.user("admin", NOW - 90 * D, admin=True)
        s.follow_show(admin, alpha, NOW - 1 * D)
        for eid in (a1, b1, g1, d1, i1):
            s.read_episode(admin, eid, NOW - 1 * D)
        s.progress(admin, d1, NOW - 1 * D, position=600, completed_at=NOW - 1 * D)

        u1, u2, u3, u4, u5 = (s.user(f"u{i}", NOW - 90 * D) for i in range(1, 6))
        # Alpha: followers u1 (old), u2, u3 (new in window) = 3, new 2.
        s.follow_show(u1, alpha, NOW - 40 * D)
        s.follow_show(u2, alpha, NOW - 5 * D)
        s.follow_show(u3, alpha, NOW - 2 * D)
        # Beta: five followers, one new; more followers than Alpha.
        s.follow_show(u1, beta, NOW - 50 * D)
        s.follow_show(u2, beta, NOW - 50 * D)
        s.follow_show(u3, beta, NOW - 60 * D)  # reads Beta only in the previous window
        s.follow_show(u4, beta, NOW - 50 * D)
        s.follow_show(u5, beta, NOW - 10 * D)
        # Gamma: u4, new. Epsilon: u5, nobody reads it.
        s.follow_show(u4, gamma, NOW - 3 * D)
        s.follow_show(u5, epsilon, NOW - 60 * D)

        # Alpha readers in the window: u1, u2 (followers) and u5 (not).
        s.read_episode(u1, a1, NOW - 1 * D)
        s.read_episode(u2, a1, NOW - 2 * D)
        s.read_episode(u5, a1, NOW - 3 * D)
        s.progress(u2, a1, NOW - 2 * D, position=1800, completed_at=NOW - 2 * D)  # listened to the end
        s.progress(u2, a2, NOW - 1 * D, position=200)  # a second Alpha episode: still one listener
        s.progress(u3, a2, NOW - 1 * D, position=0)  # "Mark unplayed": not a read, not a listen
        # Previous window: u1 read A2 (Alpha, once in each window), u3 read B1.
        s.read_episode(u1, a2, NOW - 30 * D)
        s.read_episode(u3, b1, NOW - 35 * D)
        # Gamma: u4 listened part-way (a reader with no inbox read); an older
        # read falls before both windows.
        s.progress(u4, g1, NOW - 1 * D, position=100)
        s.read_episode(u4, g1, NOW - 80 * D)
        # Delta: reached only through discovery.
        s.progress(u5, d1, NOW - 4 * D, position=50)
        # An imported episode.
        s.read_episode(u1, i1, NOW - 1 * D)

    repo = PostgresAnalyticsRepository(PG_DSN, exclude_emails=(LOCAL_EMAIL,))
    service = AnalyticsService(repo, enabled=True, clock=lambda: NOW)
    return {"service": service, "repo": repo, "tag": tag}


@pg_only
def test_content_podcasts(content):
    report = content["service"].report("28d").content
    assert [p.title for p in report.podcasts] == ["Alpha", "Gamma", "Delta", "Beta", "Epsilon"]  # no Imports, Zeta
    p = {p.title: p for p in report.podcasts}
    tag = content["tag"]

    alpha = p["Alpha"]
    assert (alpha.category, alpha.slug) == (f"Technology {tag}", "alpha")
    assert (alpha.followers, alpha.new_follows, alpha.follower_readers) == (3, 2, 2)  # u1 u2 read; u3 reset
    assert (alpha.readers, alpha.readers_previous) == (3, 1)
    assert (alpha.listeners, alpha.completions) == (1, 1)  # u2, across A1 and A2
    assert alpha.change == "+2"

    # Read only in the previous window, by a follower (u3), who is therefore
    # not a follower reader now; the admin's read never counts.
    beta = p["Beta"]
    assert (beta.category, beta.followers, beta.new_follows) == (f"Technology {tag}", 5, 1)
    assert (beta.readers, beta.readers_previous, beta.follower_readers, beta.listeners) == (0, 1, 0, 0)
    assert beta.change == "−1"

    gamma = p["Gamma"]  # a partial listen is a reader and a listener
    assert gamma.category is None
    assert (gamma.followers, gamma.readers, gamma.readers_previous, gamma.follower_readers) == (1, 1, 0, 1)
    assert (gamma.listeners, gamma.completions) == (1, 0)

    delta = p["Delta"]
    assert (delta.followers, delta.readers, delta.listeners) == (0, 1, 1)


@pg_only
def test_content_podcasts_are_the_union_of_both_top_lists(content):
    service, repo = content["service"], content["repo"]
    window = service.window_for("28d", now=NOW)
    report = repo.content(window, service.previous_window(window), limit=1)
    assert [p.title for p in report.podcasts] == ["Alpha", "Beta"]  # top reader, top followed


@pg_only
def test_content_episodes(content):
    episodes = content["service"].report("28d").content.episodes
    # B1 was read only in the previous window; A2 is u2's partial listen
    # (u1's read was earlier, u3's reset is not a read).
    assert [e.title for e in episodes] == ["A1", "G1", "D1", "A2", "I1"]  # ties: listeners, then newest
    e = {e.title: e for e in episodes}
    assert (e["A1"].readers, e["A1"].listeners, e["A1"].completions) == (3, 1, 1)
    assert (e["A1"].podcast_slug, e["A1"].slug, e["A1"].imported) == ("alpha", "a1", False)
    assert e["A1"].published_at == NOW - 6 * D
    assert (e["I1"].imported, e["I1"].readers, e["I1"].listeners) == (True, 1, 0)


@pg_only
def test_content_categories_roll_up_and_add_up(content):
    tag = content["tag"]
    categories = content["service"].report("28d").content.categories
    assert [c.category for c in categories] == [f"Technology {tag}", UNCATEGORISED, f"Comedy {tag}", f"News {tag}"]
    c = {c.category: c for c in categories}
    tech = c[f"Technology {tag}"]  # Alpha + Beta (via its subcategory)
    assert (tech.shows, tech.shows_read, tech.followers) == (2, 1, 5)
    assert (tech.readers, tech.readers_previous) == (3, 2)  # u1 u2 u5; before: u1 (A2), u3 (B1)
    assert (c[UNCATEGORISED].shows, c[UNCATEGORISED].followers, c[UNCATEGORISED].readers) == (1, 1, 1)
    comedy = c[f"Comedy {tag}"]  # read through discovery only
    assert (comedy.shows, comedy.shows_read, comedy.followers, comedy.readers) == (0, 1, 0, 1)
    news = c[f"News {tag}"]  # follows alone list a category
    assert (news.shows, news.shows_read, news.followers, news.readers, news.change) == (1, 0, 1, 0, "—")
    assert sum(x.shows for x in categories) == 4  # Alpha, Beta, Gamma, Epsilon: every followed show once


def test_unavailable_stub_raises_and_service_reports_unavailable():
    stub = UnavailableAnalyticsRepository()
    assert stub.available is False
    with pytest.raises(AnalyticsUnavailableError):
        stub.cohorts(NOW)
    window = AnalyticsService(stub, enabled=True).window_for("7d", now=NOW)
    with pytest.raises(AnalyticsUnavailableError):
        stub.content(window, window, limit=20)
    service = AnalyticsService(stub, enabled=True, clock=lambda: NOW)
    assert service.available is False
    with pytest.raises(AnalyticsUnavailableError):
        service.report("28d")
