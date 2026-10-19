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

"""Spec #104 Phase 1 over HTTP: disabled accounts, panic mode, the follow
gate and the signup gate, in a multi-user app wired like production."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.unit.oauth_fakes import install_fake_google
from thestill.models.podcast import Podcast
from thestill.models.user import User, UserStatus
from thestill.repositories.factory import make_repositories
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.wiring import build_abuse_services
from thestill.services.auth_service import AuthService
from thestill.services.follower_service import FollowerService
from thestill.utils.config import Config
from thestill.utils.datetime_utils import now_utc
from thestill.web.app import create_app
from thestill.web.dependencies import AUTH_COOKIE_NAME
from thestill.web.middleware import rate_limit


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
        admin_contact_email="ops@thestill.test",
    )


@pytest.fixture
def app_state(app_state, app_config):
    """The shared fixture, with the spec #104 services wired as create_app does."""
    abuse = build_abuse_services(app_config, make_repositories(app_config))
    app_state.abuse = abuse
    app_state.auth_service = AuthService(app_config, app_state.user_repository, signup_policy=abuse.signup)
    app_state.follower_service = FollowerService(
        app_state.follower_repository, app_state.repository, follow_policy=abuse.follow_policy
    )
    return app_state


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    rate_limit.reset_for_testing()
    yield
    rate_limit.reset_for_testing()


@pytest.fixture
def client(app_config, app_state) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app)


def _user(app_state, email: str, *, is_admin: bool = False) -> User:
    app_state.user_repository.save(User(email=email, is_admin=is_admin))
    return app_state.user_repository.get_by_email(email)


def _sign_in(client, app_state, user: User) -> None:
    client.cookies.set(AUTH_COOKIE_NAME, app_state.auth_service.create_jwt(user))


def _suspend(app_state, user: User) -> None:
    app_state.user_repository.apply_status(
        user.id, UserStatus.SUSPENDED, reason="spam", changed_by=None, changed_at=now_utc()
    )


def _podcast(app_state, slug: str) -> Podcast:
    podcast = Podcast(rss_url=f"https://example.com/{slug}.xml", title=slug, description="", slug=slug)
    app_state.repository.save(podcast)
    return app_state.repository.get_by_url(f"https://example.com/{slug}.xml")


# -- disabled accounts --------------------------------------------------------


def test_status_reads_signed_out_with_a_flag_for_a_disabled_account(client, app_state):
    user = _user(app_state, "u@example.com")
    _sign_in(client, app_state, user)
    body = client.get("/api/auth/status").json()
    assert body["authenticated"] is True and body["account_disabled"] is False
    assert "status_reason" not in body["user"] and "tokens_valid_after" not in body["user"]

    _suspend(app_state, user)
    body = client.get("/api/auth/status").json()
    assert body["authenticated"] is False
    assert body["user"] is None
    assert body["account_disabled"] is True
    assert body["admin_contact_email"] == "ops@thestill.test"


def test_cookie_and_bearer_of_a_disabled_account_get_403(client, app_state):
    user = _user(app_state, "u@example.com")
    token = app_state.auth_service.create_jwt(user)
    _suspend(app_state, user)

    by_cookie = client.get("/api/podcasts", cookies={AUTH_COOKIE_NAME: token})
    by_bearer = client.get("/api/podcasts", headers={"Authorization": f"Bearer {token}"})
    for response in (by_cookie, by_bearer):
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "account_disabled"


def test_logout_everywhere_signs_the_session_out(client, app_state):
    user = _user(app_state, "u@example.com")
    _sign_in(client, app_state, user)
    assert client.get("/api/podcasts").status_code == 200
    app_state.abuse.account_admin.logout_everywhere(user.id, actor=Actor.cli())
    assert client.get("/api/podcasts").status_code == 401


# -- panic mode ---------------------------------------------------------------


def test_read_only_blocks_non_admin_writes_but_not_reads_or_admins(client, app_state):
    reader = _user(app_state, "reader@example.com")
    admin = _user(app_state, "op@example.com", is_admin=True)
    _podcast(app_state, "show")
    app_state.abuse.settings.set("read_only", True, actor=Actor.cli())

    _sign_in(client, app_state, reader)
    refused = client.post("/api/podcasts/show/follow")
    assert refused.status_code == 503
    assert refused.json()["detail"] == {
        "error": "read_only",
        "message": "Thestill is read-only right now. Please try again later.",
    }
    assert client.get("/api/podcasts").status_code == 200

    _sign_in(client, app_state, admin)
    # The admin passes the method filter; the follow gate itself still
    # applies (read_only pauses new feeds for everyone).
    response = client.post("/api/podcasts/show/follow")
    assert response.status_code == 503 and response.json()["detail"]["error"] == "read_only"
    app_state.abuse.settings.set("read_only", False, actor=Actor.cli())
    assert client.post("/api/podcasts/show/follow").status_code == 201


# -- follow gate ---------------------------------------------------------------


def test_new_feeds_paused_refuses_only_the_first_follow(client, app_state):
    first, second = _user(app_state, "a@example.com"), _user(app_state, "b@example.com")
    _podcast(app_state, "dormant")
    popular = _podcast(app_state, "popular")
    app_state.follower_service.follow(first.id, popular.id)
    app_state.abuse.settings.set("new_feeds_enabled", False, actor=Actor.cli())

    _sign_in(client, app_state, second)
    refused = client.post("/api/podcasts/dormant/follow")
    assert refused.status_code == 503
    assert refused.json()["detail"]["feature"] == "new_feeds"
    assert client.post("/api/podcasts/popular/follow").status_code == 201


# -- OAuth callback -----------------------------------------------------------


def _callback(client):
    client.cookies.set("oauth_state", "s")
    return client.get("/api/auth/google/callback?code=c&state=s", follow_redirects=False)


def test_callback_redirects_a_disabled_account_to_login(client, app_state, monkeypatch):
    install_fake_google(monkeypatch, email="u@example.com")
    user = _user(app_state, "u@example.com")
    _suspend(app_state, user)
    response = _callback(client)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=account_disabled"
    assert AUTH_COOKIE_NAME not in response.cookies


def test_callback_redirects_a_refused_signup_to_login(client, app_state, monkeypatch):
    install_fake_google(monkeypatch, email="u@example.com")
    app_state.abuse.settings.set("signup_mode", "closed", actor=Actor.cli())
    response = _callback(client)
    assert response.status_code == 302
    assert response.headers["location"] == "/login?error=signups_closed"
    assert app_state.user_repository.get_by_email("u@example.com") is None


# -- quotas and per-user rate limits (Phase 2) ---------------------------------


def test_follow_quota_answers_429_with_retry_after(client, app_state, app_config):
    app_config.quota_feeds_introduced_per_day = 1
    user = _user(app_state, "u@example.com")
    _podcast(app_state, "first")
    _podcast(app_state, "second")
    _sign_in(client, app_state, user)

    assert client.post("/api/podcasts/first/follow").status_code == 201
    refused = client.post("/api/podcasts/second/follow")
    assert refused.status_code == 429
    detail = refused.json()["detail"]
    assert detail["error"] == "quota_exceeded" and detail["quota"] == "feeds_introduced_per_day"
    assert detail["limit"] == 1 and detail["retry_after"] > 0
    assert refused.headers["Retry-After"] == str(detail["retry_after"])
    assert detail["message"].startswith("You've added 1 new podcasts today.")


def test_per_user_rate_limit(client, app_state, monkeypatch):
    from thestill.web import dependencies
    from thestill.web.middleware.rate_limit import RateLimit, user_rate_limit_hits

    monkeypatch.setattr(dependencies, "USER_API_LIMIT", RateLimit(max_events=3, window_seconds=60))
    alice, bob = _user(app_state, "alice@example.com"), _user(app_state, "bob@example.com")
    _sign_in(client, app_state, alice)
    assert [client.get("/api/podcasts").status_code for _ in range(4)] == [200, 200, 200, 429]
    limited = client.get("/api/podcasts")
    assert limited.json()["detail"]["error"] == "rate_limited"
    assert limited.headers["Retry-After"] == "60"
    assert user_rate_limit_hits()[alice.id] == 2

    _sign_in(client, app_state, bob)  # another user's budget is untouched
    assert client.get("/api/podcasts").status_code == 200


def test_search_has_a_tighter_per_user_budget(client, app_state, monkeypatch):
    from thestill.web import dependencies
    from thestill.web.middleware.rate_limit import RateLimit

    monkeypatch.setattr(dependencies, "USER_SEARCH_LIMIT", RateLimit(max_events=1, window_seconds=60))
    _sign_in(client, app_state, _user(app_state, "u@example.com"))
    first = client.get("/api/search/quick?q=climate")
    assert first.status_code != 429
    assert client.get("/api/search/quick?q=climate").status_code == 429
    assert client.get("/api/podcasts").status_code == 200  # the general budget is separate
