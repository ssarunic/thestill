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

"""Spec #104 §1.1–§1.2 — account status and the signup gate in AuthService."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.unit.oauth_fakes import install_fake_google
from thestill.models.user import User, UserStatus
from thestill.services.abuse.actor import Actor
from thestill.services.abuse.errors import AccountDisabledError, SignupRefusedError
from thestill.services.auth_service import AuthService
from thestill.utils.datetime_utils import now_utc

from .conftest import NOW


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def auth(world, monkeypatch):
    install_fake_google(monkeypatch, email="user@example.com")
    world.config.google_client_id = "id"
    world.config.google_client_secret = "secret"
    return AuthService(world.config, world.repos.user, signup_policy=world.abuse.signup)


def _disable(world, user_id: str, status: UserStatus = UserStatus.SUSPENDED) -> None:
    world.repos.user.apply_status(user_id, status, reason="spam", changed_by=None, changed_at=NOW)


@pytest.mark.anyio
async def test_disabled_user_cannot_sign_in_and_leaves_no_trace(auth, world, monkeypatch):
    user, _, _ = await auth.handle_google_callback(code="c", redirect_uri="r")
    _disable(world, user.id)
    before = world.repos.user.get_by_id(user.id).last_login_at

    with pytest.raises(AccountDisabledError):
        await auth.handle_google_callback(code="c", redirect_uri="r")
    assert world.repos.user.get_by_id(user.id).last_login_at == before


@pytest.mark.anyio
async def test_disabled_user_cannot_sign_in_through_email_linking(auth, world, monkeypatch):
    world.repos.user.save(User(email="linked@example.com"))
    linked = world.repos.user.get_by_email("linked@example.com")
    _disable(world, linked.id, UserStatus.BANNED)
    install_fake_google(monkeypatch, sub="g-linked", email="linked@example.com")
    with pytest.raises(AccountDisabledError):
        await auth.handle_google_callback(code="c", redirect_uri="r")


@pytest.mark.anyio
async def test_signup_gate_refuses_new_identities_only(auth, world, monkeypatch):
    await auth.handle_google_callback(code="c", redirect_uri="r")  # existing account
    world.repos.user.save(User(email="linked@example.com"))
    world.abuse.settings.set("signup_mode", "closed", actor=Actor.cli())

    # Existing user by google_id, and by email linking, still get in.
    await auth.handle_google_callback(code="c", redirect_uri="r")
    install_fake_google(monkeypatch, sub="g-linked", email="linked@example.com")
    await auth.handle_google_callback(code="c", redirect_uri="r")

    install_fake_google(monkeypatch, sub="g-new", email="new@example.com")
    with pytest.raises(SignupRefusedError):
        await auth.handle_google_callback(code="c", redirect_uri="r")
    assert world.repos.user.get_by_email("new@example.com") is None

    world.abuse.settings.set("signup_mode", "allowlist", actor=Actor.cli())
    world.abuse.settings.set("signup_allowlist", ["@example.com"], actor=Actor.cli())
    _, _, is_new = await auth.handle_google_callback(code="c", redirect_uri="r")
    assert is_new


def test_token_of_a_disabled_user_raises_instead_of_reading_signed_out(auth, world):
    user = world.add_user("u@example.com")
    token = auth.create_jwt(user)
    assert auth.get_user_from_token(token).id == user.id

    _disable(world, user.id)
    with pytest.raises(AccountDisabledError):
        auth.get_user_from_token(token)

    world.repos.user.apply_status(user.id, UserStatus.ACTIVE, reason=None, changed_by=None, changed_at=NOW)
    assert auth.get_user_from_token(token).id == user.id


def test_tokens_issued_before_tokens_valid_after_are_rejected(auth, world):
    user = world.add_user("u@example.com")
    old = auth.create_jwt(user)
    world.abuse.account_admin.logout_everywhere(user.id, actor=Actor.cli())
    assert auth.get_user_from_token(old) is None

    # A token issued once the revocation instant has passed works.
    world.repos.user.set_tokens_valid_after(user.id, now_utc() - timedelta(seconds=5))
    assert auth.get_user_from_token(auth.create_jwt(user)).id == user.id


def test_single_user_mode_ignores_status(tmp_path):
    from .conftest import make_world

    world = make_world(tmp_path, multi_user=False)
    auth = AuthService(world.config, world.repos.user)
    local = auth.get_or_create_default_user()
    _disable(world, local.id)
    auth._default_user = None
    assert auth.get_current_user(None).id == local.id
    auth.ensure_active(world.repos.user.get_by_id(local.id))  # no-op
