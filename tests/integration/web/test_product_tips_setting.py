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

"""Spec #118 — the "Product tips by email" Settings switch over the API."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from thestill.models.user import User
from thestill.utils.config import Config
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
    )


@pytest.fixture(autouse=True)
def _fresh_buckets() -> Iterator[None]:
    rate_limit.reset_for_testing()
    yield
    rate_limit.reset_for_testing()


@pytest.fixture
def client(app_config: Config, app_state) -> TestClient:
    app = create_app(app_config)
    app.state.app_state = app_state
    return TestClient(app)


def _sign_in(client: TestClient, app_state) -> User:
    app_state.auth_service.user_repository.save(User(email="reader@example.com"))
    user = app_state.auth_service.user_repository.get_by_email("reader@example.com")
    client.cookies.set(AUTH_COOKIE_NAME, app_state.auth_service.create_jwt(user))
    return user


def test_accounts_start_with_tips_off(client, app_state):
    user = _sign_in(client, app_state)
    assert user.product_tips_email_enabled is False
    assert client.get("/api/auth/me").json()["user"]["product_tips_email_enabled"] is False


def _stub_google(app_state, monkeypatch):
    """A Google callback that creates a brand-new account."""

    async def callback(code, redirect_uri, signup_source=None):
        repo = app_state.auth_service.user_repository
        repo.save(User(email="new@example.com"))
        user = repo.get_by_email("new@example.com")
        return user, app_state.auth_service.create_jwt(user), True

    async def no_region(user, ip):
        return None

    monkeypatch.setattr(app_state.auth_service, "handle_google_callback", callback)
    monkeypatch.setattr(app_state.auth_service, "maybe_infer_region", no_region)


def _google_round_trip(client):
    client.cookies.set("oauth_state", "state-1")
    return client.get("/api/auth/google/callback", params={"code": "c", "state": "state-1"}, follow_redirects=False)


def test_signup_gets_tips_on_when_the_sign_in_page_offered_them(client, app_state, monkeypatch):
    _stub_google(app_state, monkeypatch)
    app_state.lifecycle_email_service = object()  # an email provider is configured

    assert _google_round_trip(client).status_code == 302

    stored = app_state.auth_service.user_repository.get_by_email("new@example.com")
    assert stored.product_tips_email_enabled is True


def test_signup_without_email_stays_off(client, app_state, monkeypatch):
    # No provider: the sign-in page said nothing about tips, so configuring
    # email later must not write to this reader.
    _stub_google(app_state, monkeypatch)
    app_state.lifecycle_email_service = None

    assert _google_round_trip(client).status_code == 302

    stored = app_state.auth_service.user_repository.get_by_email("new@example.com")
    assert stored.product_tips_email_enabled is False


def test_switch_turns_tips_off_and_leaves_usage_events_alone(client, app_state):
    user = _sign_in(client, app_state)
    client.patch("/api/auth/me", json={"product_tips_email_enabled": True})

    body = client.patch("/api/auth/me", json={"product_tips_email_enabled": False}).json()

    assert body["user"]["product_tips_email_enabled"] is False
    assert body["user"]["usage_analytics_enabled"] is True
    stored = app_state.auth_service.user_repository.get_by_id(user.id)
    assert stored.product_tips_email_enabled is False and stored.usage_analytics_enabled is True

    assert client.patch("/api/auth/me", json={"product_tips_email_enabled": True}).json()["user"][
        "product_tips_email_enabled"
    ]


def test_a_later_sign_in_keeps_the_choice(client, app_state):
    user = _sign_in(client, app_state)
    client.patch("/api/auth/me", json={"product_tips_email_enabled": True})

    # A login re-saves the user (name, picture, last_login) from a model
    # whose default is off; the switch is not a column that upsert touches.
    repo = app_state.auth_service.user_repository
    repo.save(User(id=user.id, email=user.email, name="New Name"))

    assert repo.get_by_id(user.id).product_tips_email_enabled is True


def test_status_flags_availability(client, app_state):
    assert client.get("/api/auth/status").json()["product_tips_email_available"] is False

    app_state.lifecycle_email_service = object()
    assert client.get("/api/auth/status").json()["product_tips_email_available"] is True
