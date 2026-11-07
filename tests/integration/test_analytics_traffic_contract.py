"""Postgres contract tests for the spec #115 traffic section.

Signed-out public views from ``public_page_daily_views``: the per-day
series, the previous window and the top pages with titles and paths.
Skips (not fails) when ``TEST_DATABASE_URL`` is unset or unreachable.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from tests.integration.test_analytics_repository_contract import NOW, PG_DSN, _Seed, pg_only
from thestill.services.analytics_service import AnalyticsService

# 7d at NOW (2026-10-14 12:00 UTC): this window is 10-08..10-14 (today
# partial), the previous one 10-01..10-07.
TODAY = NOW.date()


def _d(month: int, day: int) -> date:
    return date(2026, month, day)


@pytest.fixture
def traffic():
    import psycopg

    from thestill.repositories.postgres_analytics_repository import PostgresAnalyticsRepository
    from thestill.repositories.postgres_schema import ensure_schema

    ensure_schema(PG_DSN)
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("TRUNCATE users, podcasts, episodes, public_page_daily_views CASCADE")
        s = _Seed(conn)
        alpha = s.show("Alpha")
        a1 = s.episode(alpha, "A1", NOW)
        # A re-typed entity keeps its id prefix; its page lives under the
        # current type.
        entity_id = f"topic:mistral-ai-{tag}"
        conn.execute(
            "INSERT INTO entities (id, type, canonical_name) VALUES (%s, 'company', 'Mistral AI')",
            (entity_id,),
        )
        gone = str(uuid.uuid4())  # an episode deleted since it was viewed

        s.page_views("landing", "", TODAY, 9)
        s.page_views("landing", "", _d(10, 8), 1)
        s.page_views("landing", "", _d(10, 7), 5)  # previous window
        s.page_views("landing", "", _d(9, 30), 100)  # before both windows
        s.page_views("top", "", _d(10, 10), 2)
        s.page_views("podcast", alpha, _d(10, 13), 5)
        s.page_views("podcast", alpha, _d(10, 1), 4)  # previous window
        s.page_views("episode", a1, TODAY, 3)
        s.page_views("episode", a1, _d(10, 12), 10)
        s.page_views("entity", entity_id, _d(10, 9), 7)
        s.page_views("episode", gone, _d(10, 11), 2)

    repo = PostgresAnalyticsRepository(PG_DSN)
    service = AnalyticsService(repo, enabled=True, clock=lambda: NOW)
    return {"service": service, "repo": repo, "alpha": alpha, "a1": a1, "entity_id": entity_id, "gone": gone}


@pg_only
def test_traffic_totals_and_previous_window(traffic):
    report = traffic["service"].report("7d").traffic
    # 9+1 landing, 2 top, 5 podcast, 3+10+2 episode, 7 entity; 10-07 and
    # 10-01 are the previous window, 09-30 is in neither.
    assert report.total == 39
    assert report.total_previous == 9
    assert report.by_kind == {"landing": 10, "top": 2, "podcast": 5, "episode": 15, "entity": 7}


@pg_only
def test_traffic_days_are_zero_filled_per_kind(traffic):
    days = traffic["service"].report("7d").traffic.days
    assert [d.day for d in days] == [_d(10, n) for n in range(8, 15)]
    assert days[-1].total == 12 and days[-1].by_kind["landing"] == 9 and days[-1].by_kind["episode"] == 3
    entity_day = next(d for d in days if d.day == _d(10, 9))
    assert entity_day.by_kind == {"landing": 0, "top": 0, "podcast": 0, "episode": 0, "entity": 7}
    assert sum(d.total for d in days) == 39


@pg_only
def test_traffic_top_pages_titles_paths_and_order(traffic):
    pages = traffic["service"].report("7d").traffic.top_pages
    rows = [(p.kind, p.title, p.parent_title, p.path, p.views, p.views_previous) for p in pages]
    slug = traffic["entity_id"].split(":", 1)[1]
    assert rows == [
        ("episode", "A1", "Alpha", "/podcasts/alpha/episodes/a1", 13, 0),
        ("landing", "Home", None, "/", 10, 5),
        ("entity", "Mistral AI", None, f"/entities/company/{slug}", 7, 0),
        ("podcast", "Alpha", None, "/podcasts/alpha", 5, 4),
        # A tie on views orders by kind: the deleted episode keeps its count
        # with no title or path.
        ("episode", None, None, None, 2, 0),
        ("top", "Top podcasts", None, "/top", 2, 0),
    ]
    assert pages[0].object_id == traffic["a1"] and pages[4].object_id == traffic["gone"]


@pg_only
def test_traffic_limit_and_quiet_pages(traffic):
    from thestill.models.analytics import AnalyticsWindow

    window = traffic["service"].window_for("7d", now=NOW)
    assert isinstance(window, AnalyticsWindow)
    assert [p.views for p in traffic["repo"].traffic(window, limit=2).top_pages] == [13, 10]


@pg_only
def test_traffic_ignores_pages_viewed_only_in_the_previous_window(traffic):
    import psycopg

    with psycopg.connect(PG_DSN) as conn:
        _Seed.page_views(type("S", (), {"conn": conn})(), "entity", "person:old-news", _d(10, 3), 50)
    report = traffic["service"].report("7d").traffic
    assert report.total_previous == 59
    assert all(p.object_id != "person:old-news" for p in report.top_pages)
