"""``GET /api/dashboard/analytics`` (spec #95 Phase 0)."""

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tests.unit.analytics_fakes import NOW, FakeAnalyticsRepository
from thestill.repositories.analytics_repository import UnavailableAnalyticsRepository
from thestill.services.analytics_service import AnalyticsService
from thestill.web.dependencies import require_admin
from thestill.web.routes import api_dashboard


@pytest.fixture(autouse=True)
def _fresh_cache():
    api_dashboard._analytics_cache.clear()  # noqa: SLF001
    yield
    api_dashboard._analytics_cache.clear()  # noqa: SLF001


@pytest.fixture
def repo():
    return FakeAnalyticsRepository()


@pytest.fixture
def state(repo):
    state = MagicMock()
    state.analytics_service = AnalyticsService(repo, enabled=True, clock=lambda: NOW)
    return state


@pytest.fixture
def client(state):
    app = FastAPI()
    # Mount like app.py does: router-level require_admin.
    app.include_router(api_dashboard.router, prefix="/api/dashboard", dependencies=[Depends(require_admin)])
    app.dependency_overrides[api_dashboard.get_app_state] = lambda: state
    return TestClient(app)


def test_returns_the_report(client):
    body = client.get("/api/dashboard/analytics", params={"range": "7d"}).json()
    assert body["status"] == "ok"
    assert body["available"] is True
    assert body["range"] == "7d"
    assert body["window"]["days"] == 7
    assert body["signups"]["by_source"] == {"episode": 2, "direct": 1}
    assert body["activation"]["overall"]["steps"][0]["key"] == "follow_24h"
    assert body["cohorts"]["weeks"] == [1, 2, 4, 8]
    assert body["notes"]


def test_defaults_to_28_days(client):
    assert client.get("/api/dashboard/analytics").json()["range"] == "28d"


def test_rejects_unknown_ranges(client):
    assert client.get("/api/dashboard/analytics", params={"range": "1y"}).status_code == 422


def test_cached_per_range(client, repo):
    client.get("/api/dashboard/analytics", params={"range": "7d"})
    client.get("/api/dashboard/analytics", params={"range": "7d"})
    assert sum(1 for name, _ in repo.calls if name == "signups") == 1
    client.get("/api/dashboard/analytics", params={"range": "28d"})
    assert sum(1 for name, _ in repo.calls if name == "signups") == 2


@pytest.mark.parametrize(
    "service",
    [
        None,
        AnalyticsService(UnavailableAnalyticsRepository(), enabled=True),
        AnalyticsService(FakeAnalyticsRepository(), enabled=False),
    ],
    ids=["unwired", "sqlite", "single-user"],
)
def test_unavailable_is_a_200_saying_so(client, state, service):
    state.analytics_service = service
    response = client.get("/api/dashboard/analytics")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert "signups" not in response.json()
