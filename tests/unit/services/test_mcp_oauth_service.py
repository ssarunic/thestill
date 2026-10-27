"""McpOAuthService: scope policy, approval, codes, refresh rotation (spec #109)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from thestill.models.user import User
from thestill.repositories.sqlite_oauth_repository import SqliteOAuthRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.repositories.sqlite_user_repository import SqliteUserRepository
from thestill.services.mcp_oauth_service import (
    McpOAuthService,
    OAuthRequestError,
    approved_scopes,
    client_scope_ceiling,
    with_query,
)
from thestill.services.oauth_cimd import CimdResolver, FetchedDocument

CLIENT = "https://claude.ai/oauth/mcp-oauth-client-metadata"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "https://thestill.me/mcp"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "t.db")
    SqlitePodcastRepository(db_path=db)
    repo = SqliteOAuthRepository(db_path=db)
    users = SqliteUserRepository(db_path=db)
    clock = Clock()
    active = {"ok": True}
    doc = {
        "client_id": CLIENT,
        "client_name": "Claude",
        "redirect_uris": [REDIRECT],
        "token_endpoint_auth_method": "none",
    }
    cimd = CimdResolver(repo, fetch=lambda url: FetchedDocument(dict(doc), timedelta(hours=1)), clock=clock)
    service = McpOAuthService(
        repo,
        resource_url=RESOURCE,
        refresh_ttl_days=90,
        is_user_active=lambda _uid: active["ok"],
        cimd=cimd,
        clock=clock,
    )
    user = users.save(User(email="ada@example.com", name="Ada"))
    return service, repo, user, clock, active


def _park(service, scopes=("read", "offline_access")):
    return service.park(
        client_id=CLIENT,
        redirect_uri=REDIRECT,
        redirect_uri_explicit=True,
        state="st",
        scopes=list(scopes) if scopes is not None else None,
        code_challenge="c" * 43,
        resource=RESOURCE,
    )


def _code_from(url):
    query = parse_qs(urlsplit(url).query)
    assert query["state"] == ["st"]
    return query["code"][0]


def _connect(service, user, *, scopes=("read", "offline_access"), ticked=("read",), is_admin=False):
    request_id = _park(service, scopes)
    redirect = service.approve(request_id, user_id=user.id, is_admin=is_admin, ticked=ticked)
    code = _code_from(redirect)
    assert service.load_code(CLIENT, code) is not None
    return service.exchange_code(code)


# -- scope policy ------------------------------------------------------------------


def test_approved_scopes_keeps_offline_access_outside_normalize():
    assert approved_scopes(["read", "offline_access"], ["read"], is_admin=False) == ("read", "offline_access")
    assert approved_scopes(["read"], ["read"], is_admin=False) == ("read",)


def test_approved_scopes_narrowed_to_request_and_admin():
    # follows ticked but not requested: dropped. pipeline for a non-admin: dropped.
    assert approved_scopes(["read"], ["read", "follows"], is_admin=False) == ("read",)
    assert approved_scopes(["read", "pipeline"], ["pipeline"], is_admin=False) == ("read",)
    assert approved_scopes(["read", "pipeline"], ["pipeline"], is_admin=True) == ("read", "pipeline")
    # read is always granted, even unticked.
    assert approved_scopes(["follows"], [], is_admin=False) == ("read",)


def test_client_scope_ceiling():
    assert client_scope_ceiling(None) == "read follows pipeline offline_access"
    assert client_scope_ceiling("read offline_access email") == "read offline_access"
    assert client_scope_ceiling("email profile") is None


def test_with_query_keeps_existing_query():
    assert with_query("https://x.example/cb?a=1", code="c", state=None) == "https://x.example/cb?a=1&code=c"


# -- authorization ---------------------------------------------------------------------


def test_park_refuses_foreign_resource(env):
    service, *_ = env
    with pytest.raises(OAuthRequestError):
        service.park(
            client_id=CLIENT,
            redirect_uri=REDIRECT,
            redirect_uri_explicit=True,
            state=None,
            scopes=["read"],
            code_challenge="c" * 43,
            resource="https://evil.example/mcp",
        )


def test_park_without_scope_defaults_to_read_offline(env):
    service, *_ = env
    view = service.view_pending(_park(service, scopes=None))
    assert view.requested_scopes == ("read",) and view.offline_access is True
    assert view.client_name == "Claude" and view.redirect_host == "claude.ai" and view.loopback_only is False


def test_approve_issues_code_once(env):
    service, _, user, *_ = env
    request_id = _park(service)
    assert service.approve(request_id, user_id=user.id, is_admin=False, ticked=["read"]) is not None
    assert service.approve(request_id, user_id=user.id, is_admin=False, ticked=["read"]) is None
    assert service.view_pending(request_id) is None


def test_deny_redirects_with_access_denied(env):
    service, *_ = env
    url = service.deny(_park(service))
    assert parse_qs(urlsplit(url).query) == {"error": ["access_denied"], "state": ["st"]}


def test_pending_expires(env):
    service, _, user, clock, _ = env
    request_id = _park(service)
    clock.advance(minutes=11)
    assert service.approve(request_id, user_id=user.id, is_admin=False, ticked=["read"]) is None


# -- codes and tokens ----------------------------------------------------------------------


def test_offline_access_earns_refresh_token(env):
    service, _, user, *_ = env
    issued = _connect(service, user)
    assert issued.refresh_token is not None and issued.scopes == ("read", "offline_access")
    other = _connect(service, user, scopes=("read",))
    assert other.refresh_token is None and other.scopes == ("read",)


def test_code_replay_revokes_grant(env):
    service, _, user, *_ = env
    code = _code_from(service.approve(_park(service), user_id=user.id, is_admin=False, ticked=["read"]))
    issued = service.exchange_code(code)
    assert service.verify_access(issued.access_token) is not None
    assert service.load_code(CLIENT, code) is None  # replay
    assert service.verify_access(issued.access_token) is None


def test_code_bound_to_client(env):
    service, _, user, *_ = env
    code = _code_from(service.approve(_park(service), user_id=user.id, is_admin=False, ticked=["read"]))
    assert service.load_code("someone-else", code) is None
    assert service.load_code(CLIENT, code) is not None


def test_two_authorizations_with_different_scopes(env):
    """Spec #109 acceptance: approving more never widens an earlier credential."""
    service, _, user, *_ = env
    t1 = _connect(service, user, scopes=("read", "follows", "offline_access"), ticked=["read"])
    # An unredeemed code from a read-only approval...
    stale_code = _code_from(
        service.approve(
            _park(service, ("read", "follows", "offline_access")), user_id=user.id, is_admin=False, ticked=["read"]
        )
    )
    # ...then the reader approves again with follows.
    wide_code = _code_from(
        service.approve(
            _park(service, ("read", "follows", "offline_access")),
            user_id=user.id,
            is_admin=False,
            ticked=["read", "follows"],
        )
    )
    assert service.verify_access(t1.access_token) is None
    assert service.load_code(CLIENT, stale_code) is None
    with pytest.raises(OAuthRequestError):
        service.exchange_code(stale_code)
    t2 = service.exchange_code(wide_code)
    token, _ = service.verify_access(t2.access_token)
    assert token.scopes == ("read", "follows", "offline_access")
    # Approving read alone again revokes the follows tokens.
    _connect(service, user, scopes=("read", "follows", "offline_access"), ticked=["read"])
    assert service.verify_access(t2.access_token) is None


def test_refresh_rotates_and_keeps_its_scopes(env):
    service, _, user, clock, _ = env
    first = _connect(service, user, scopes=("read", "follows", "offline_access"), ticked=["read", "follows"])
    clock.advance(minutes=55)
    record = service.load_refresh(CLIENT, first.refresh_token)
    second = service.exchange_refresh(first.refresh_token, ["read"])
    assert record is not None and second.refresh_token and second.refresh_token != first.refresh_token
    access, _ = service.verify_access(second.access_token)
    assert access.scopes == ("read",)
    # The new refresh token keeps the full set.
    third = service.exchange_refresh(second.refresh_token, [])
    access3, _ = service.verify_access(third.access_token)
    assert access3.scopes == ("read", "follows", "offline_access")


def test_refresh_replay_revokes_grant(env):
    service, _, user, *_ = env
    first = _connect(service, user)
    second = service.exchange_refresh(first.refresh_token, [])
    assert service.load_refresh(CLIENT, first.refresh_token) is None  # replay
    assert service.verify_access(second.access_token) is None
    assert service.load_refresh(CLIENT, second.refresh_token) is None


def test_inactive_user_cannot_refresh_or_redeem(env):
    service, _, user, _, active = env
    first = _connect(service, user)
    code = _code_from(service.approve(_park(service), user_id=user.id, is_admin=False, ticked=["read"]))
    active["ok"] = False
    assert service.load_refresh(CLIENT, first.refresh_token) is None
    assert service.load_code(CLIENT, code) is None


def test_refresh_expires_after_ttl(env):
    service, _, user, clock, _ = env
    first = _connect(service, user)
    clock.advance(days=91)
    assert service.load_refresh(CLIENT, first.refresh_token) is None


def test_revoke_token_disconnects(env):
    service, _, user, *_ = env
    issued = _connect(service, user)
    service.revoke_token(issued.refresh_token)
    assert service.verify_access(issued.access_token) is None
    assert service.list_grants(user.id) == []


def test_disconnect(env):
    service, _, user, *_ = env
    issued = _connect(service, user)
    [grant] = service.list_grants(user.id)
    assert service.disconnect("someone-else", grant.id) is False
    assert service.disconnect(user.id, grant.id) is True
    assert service.verify_access(issued.access_token) is None


def test_refresh_lifetime_slides_with_use(env):
    """90 days since last use, not since consent (spec #109 decision 1)."""
    service, _, user, clock, _ = env
    token = _connect(service, user).refresh_token
    for _ in range(3):  # day 80, 160, 240
        clock.advance(days=80)
        assert service.load_refresh(CLIENT, token) is not None
        token = service.exchange_refresh(token, []).refresh_token


def test_dcr_refuses_unsafe_redirect_uris(env):
    service, *_ = env
    for uri in ("javascript:alert(1)//", "http://evil.example/cb", "data:text/html,x", "https://x.example/cb#frag"):
        with pytest.raises(OAuthRequestError) as exc:
            service.register_client("bad", {"client_id": "bad", "redirect_uris": [uri]})
        assert exc.value.error == "invalid_redirect_uri"
    service.register_client("ok", {"client_id": "ok", "redirect_uris": ["com.example.app:/cb", "http://127.0.0.1/cb"]})


def test_approve_refused_when_client_no_longer_resolves(env):
    """A registration pruned (or a document gone) between /authorize and
    the reader's click gets no code."""
    service, _, user, *_ = env
    request_id = service.park(
        client_id="dcr-pruned",
        redirect_uri="http://localhost:9999/cb",
        redirect_uri_explicit=True,
        state="st",
        scopes=["read"],
        code_challenge="c" * 43,
        resource=RESOURCE,
    )
    assert service.approve(request_id, user_id=user.id, is_admin=False, ticked=["read"]) is None


def test_dcr_client_round_trip(env):
    service, *_ = env
    service.register_client(
        "abc", {"client_id": "abc", "redirect_uris": ["http://localhost/cb"], "client_name": "Inspector"}
    )
    assert service.get_client("abc").client_name == "Inspector"
    assert service.get_client("unknown") is None
    with pytest.raises(OAuthRequestError):
        service.register_client("https://x.example/doc", {})


def test_concurrent_refresh_reuse_revokes_the_grant(env):
    """Codex P2 on #442: two requests with one refresh token both pass
    load_refresh; the loser of the rotation must revoke the grant, not just
    fail, or a stolen token that wins the race stays usable."""
    service, _, user, *_ = env
    first = _connect(service, user)
    assert service.load_refresh(CLIENT, first.refresh_token) is not None
    assert service.load_refresh(CLIENT, first.refresh_token) is not None  # both pass before either rotates
    winner = service.exchange_refresh(first.refresh_token, [])
    with pytest.raises(OAuthRequestError):
        service.exchange_refresh(first.refresh_token, [])
    assert service.verify_access(winner.access_token) is None
    assert service.load_refresh(CLIENT, winner.refresh_token) is None


def test_concurrent_code_reuse_revokes_the_grant(env):
    service, _, user, *_ = env
    code = _code_from(service.approve(_park(service), user_id=user.id, is_admin=False, ticked=["read"]))
    assert service.load_code(CLIENT, code) is not None
    assert service.load_code(CLIENT, code) is not None
    winner = service.exchange_code(code)
    with pytest.raises(OAuthRequestError):
        service.exchange_code(code)
    assert service.verify_access(winner.access_token) is None
