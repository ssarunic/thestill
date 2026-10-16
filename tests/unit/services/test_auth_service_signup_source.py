"""``AuthService.handle_google_callback`` records the signup source once (spec #95)."""

from types import SimpleNamespace

import pytest

from thestill.models.user import User
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services import auth_service as auth_module
from thestill.services.auth_service import AuthService


class _FakeOAuthClient:
    """Stands in for authlib's AsyncOAuth2Client: one Google identity."""

    userinfo = {"sub": "g-new", "email": "new@example.com", "name": "New", "picture": None}

    def __init__(self, **_):
        self.token = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetch_token(self, *_, **__):
        return {"access_token": "t"}

    async def get(self, _url):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: dict(self.userinfo))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def users(tmp_path):
    db = str(tmp_path / "auth.db")
    SqlitePodcastRepository(db_path=db)
    return SqliteUserRepository(db_path=db)


@pytest.fixture
def auth(users, monkeypatch):
    monkeypatch.setattr(auth_module, "AsyncOAuth2Client", _FakeOAuthClient)
    config = SimpleNamespace(
        multi_user=True,
        jwt_secret_key="k" * 32,
        jwt_algorithm="HS256",
        jwt_expire_days=30,
        google_client_id="id",
        google_client_secret="secret",
    )
    return AuthService(config, users)


@pytest.mark.anyio
async def test_new_account_records_its_signup_source(auth, users):
    user, _, is_new = await auth.handle_google_callback(code="c", redirect_uri="r", signup_source="episode")
    assert is_new is True
    assert users.get_by_email("new@example.com").signup_source == "episode"


@pytest.mark.anyio
async def test_returning_and_linked_accounts_keep_theirs(auth, users, monkeypatch):
    await auth.handle_google_callback(code="c", redirect_uri="r", signup_source="episode")
    await auth.handle_google_callback(code="c", redirect_uri="r", signup_source="top")
    assert users.get_by_email("new@example.com").signup_source == "episode"

    # Account linking: an existing row signs in with Google for the first time.
    users.save(User(email="linked@example.com"))
    linked = {"sub": "g-linked", "email": "linked@example.com", "name": "L", "picture": None}
    monkeypatch.setattr(_FakeOAuthClient, "userinfo", linked)
    _, _, is_new = await auth.handle_google_callback(code="c", redirect_uri="r", signup_source="podcast")
    assert is_new is False
    assert users.get_by_email("linked@example.com").signup_source is None
