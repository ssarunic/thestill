"""AnalyticsService: range → window, availability, report assembly (spec #95)."""

from datetime import datetime, timezone

import pytest

from tests.unit.analytics_fakes import NOW, FakeAnalyticsRepository
from thestill.repositories.analytics_repository import AnalyticsUnavailableError
from thestill.services.analytics_service import NOTES, AnalyticsService, InvalidRangeError


@pytest.fixture
def repo():
    return FakeAnalyticsRepository()


def test_window_is_whole_utc_days_ending_now(repo):
    service = AnalyticsService(repo, enabled=True)
    window = service.window_for("7d", now=NOW)
    assert window.start == datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
    assert window.end == NOW
    assert window.days == 7
    assert service.window_for("90d", now=NOW).start == datetime(2026, 7, 17, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["5d", "1m", "", "28"])
def test_rejects_ranges_outside_the_allowlist(repo, bad):
    with pytest.raises(InvalidRangeError):
        AnalyticsService(repo, enabled=True).window_for(bad, now=NOW)


def test_report_assembles_all_four_reads_with_one_clock(repo):
    report = AnalyticsService(repo, enabled=True, clock=lambda: NOW).report("28d")
    assert report.range == "28d"
    assert report.generated_at == NOW
    assert report.signups.total == 3
    assert report.engagement.opened_ratio == 0.75
    assert report.notes == NOTES
    windows = {name: arg for name, arg in repo.calls}
    assert windows["signups"] == windows["activation"] == windows["engagement"] == report.window
    assert windows["cohorts"] == NOW


def test_single_user_mode_is_unavailable_even_with_a_working_repository(repo):
    service = AnalyticsService(repo, enabled=False, clock=lambda: NOW)
    assert service.available is False
    with pytest.raises(AnalyticsUnavailableError):
        service.report()
    assert repo.calls == []
