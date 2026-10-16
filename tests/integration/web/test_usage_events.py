"""Spec #95 Layer B wiring through the real app: the public page-view
beacon, sign-in events, the analytics key on /api/auth/status and the
Settings switch on PATCH /api/auth/me."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.unit.analytics_fakes import RecordingSink
from thestill.models.user import User
from thestill.utils.config import Config
from thestill.web.app import create_app
from thestill.web.dependencies import AUTH_COOKIE_NAME
from thestill.web.middleware import rate_limit
from thestill.web.public_access import clear_public_caches

BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/129.0 Safari/537.36"


@pytest.fixture
def app_config(tmp_path: Path) -> Config:
    return Config(
        storage_path=tmp_path,
        multi_user=True,
        public_base_url="https://thestill.test",
        google_client_id="test-client",
        google_client_secret="test-secret",
        jwt_secret_key="test" * 16,
        cookie_secure=False,
    )


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    rate_limit.reset_for_testing()
    clear_public_caches()
    yield
    rate_limit.reset_for_testing()


@pytest.fixture
def sink(app_state) -> RecordingSink:
    app_state.analytics_sink = RecordingSink()
    return app_state.analytics_sink


@pytest.fixture
def client(app_config: Config, app_state, sink) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app, headers={"User-Agent": BROWSER})


def _sign_in(client: TestClient, app_state, **fields) -> User:
    user = app_state.auth_service.user_repository.save(User(email="reader@example.com", region="gb", **fields))
    user = app_state.auth_service.user_repository.get_by_email("reader@example.com")
    client.cookies.set(AUTH_COOKIE_NAME, app_state.auth_service.create_jwt(user))
    return user


# -- public page views -------------------------------------------------------


def test_public_page_view_is_counted_with_kind_and_external_referrer(client, sink):
    response = client.post(
        "/api/public/pageview",
        json={"path": "/podcasts/hard-fork/episodes/ep-1?ref=x", "referrer": "https://news.ycombinator.com/item?id=1"},
    )
    assert response.status_code == 204
    [call] = sink.captured
    assert call["event"].name == "public_page_view"
    assert call["event"].kind == "episode"
    assert call["event"].referrer_host == "news.ycombinator.com"
    assert call["user"] is None and call["visitor_key"]


@pytest.mark.parametrize("referrer", ["https://thestill.test/top", "https://testserver/", "javascript:alert(1)", None])
def test_own_or_junk_referrers_are_dropped(client, sink, referrer):
    client.post("/api/public/pageview", json={"path": "/top", "referrer": referrer})
    assert sink.captured[0]["event"].referrer_host is None


def test_unknown_paths_bots_and_signed_in_readers_send_nothing(client, sink, app_state):
    assert client.post("/api/public/pageview", json={"path": "/inbox"}).status_code == 204
    bot = client.post(
        "/api/public/pageview", json={"path": "/top"}, headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"}
    )
    assert bot.status_code == 204
    _sign_in(client, app_state)
    assert client.post("/api/public/pageview", json={"path": "/top"}).status_code == 204
    assert sink.captured == []


def test_gpc_visitor_gets_no_visitor_key(client, sink):
    client.post("/api/public/pageview", json={"path": "/"}, headers={"Sec-GPC": "1"})
    assert sink.captured[0]["visitor_key"] is None and sink.captured[0]["gpc"] is True


def test_oversized_page_view_is_rejected(client, sink):
    assert client.post("/api/public/pageview", json={"path": "/" + "x" * 600}).status_code == 422
    assert sink.captured == []


# -- sign-in -----------------------------------------------------------------


def test_sign_in_reports_where_it_started_and_completed(client, sink, app_state, monkeypatch):
    auth = app_state.auth_service
    monkeypatch.setattr(auth, "get_google_auth_url", lambda redirect_uri: ("https://accounts.example/o", "st"))
    user = auth.user_repository.save(User(email="new@example.com"))

    async def callback(code, redirect_uri, signup_source=None):
        return user, "jwt-token", True

    async def no_region(user, ip):
        return None

    monkeypatch.setattr(auth, "handle_google_callback", callback)
    monkeypatch.setattr(auth, "maybe_infer_region", no_region)

    client.get("/api/auth/google/login", params={"next": "/entities/person/x"}, follow_redirects=False)
    done = client.get("/api/auth/google/callback", params={"code": "c", "state": "st"}, follow_redirects=False)
    assert done.status_code == 302

    started, completed = sink.captured
    assert (started["event"].name, started["event"].from_kind, started["user"]) == ("login_started", "entity", None)
    assert (completed["event"].name, completed["event"].next_kind) == ("login_completed", "entity")
    assert completed["user"].id == user.id


# -- /api/auth/status and the Settings switch --------------------------------


def test_status_hands_the_key_to_a_signed_in_reader_only(client, app_state):
    anon = client.get("/api/auth/status").json()
    assert anon["analytics"] is None and anon["usage_analytics_available"] is True

    _sign_in(client, app_state)
    signed_in = client.get("/api/auth/status").json()
    assert signed_in["analytics"] == {"key": "phc_test"}
    assert signed_in["user"]["usage_analytics_enabled"] is True

    assert client.get("/api/auth/status", headers={"Sec-GPC": "1"}).json()["analytics"] is None


def test_settings_switch_turns_events_off_and_keeps_the_region(client, app_state):
    user = _sign_in(client, app_state)
    app_state.auth_service.set_user_region(user, "gb")

    body = client.patch("/api/auth/me", json={"usage_analytics_enabled": False}).json()
    assert body["user"]["usage_analytics_enabled"] is False
    assert body["user"]["region"] == "gb"  # untouched: only the fields sent change

    assert client.get("/api/auth/status").json()["analytics"] is None
    stored = app_state.auth_service.user_repository.get_by_id(user.id)
    assert stored.usage_analytics_enabled is False and stored.region == "gb"

    assert client.patch("/api/auth/me", json={"region": None}).json()["user"]["region"] is None


def test_status_without_usage_events(client, app_state):
    app_state.analytics_sink = SimpleNamespace(enabled=False, browser_key=None)
    _sign_in(client, app_state)
    body = client.get("/api/auth/status").json()
    assert body["analytics"] is None and body["usage_analytics_available"] is False
