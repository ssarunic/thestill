"""Spec #95 Layer B wiring through the real app: the public page-view
beacon, sign-in events, the analytics key on /api/auth/status and the
Settings switch on PATCH /api/auth/me."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.unit.analytics_fakes import RecordingSink
from thestill.models.podcast import Episode, Podcast
from thestill.models.user import User
from thestill.repositories.sqlite_public_page_view_repository import SqlitePublicPageViewRepository
from thestill.services.abuse.errors import AccountDisabledError, SignupRefusedError
from thestill.utils.config import Config
from thestill.utils.sqlite_ext import connect
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


def _stub_oauth(app_state, monkeypatch, user, states):
    """Google round trip without Google: each login mints the next state."""
    auth = app_state.auth_service
    pending = iter(states)
    monkeypatch.setattr(auth, "get_google_auth_url", lambda redirect_uri: ("https://accounts.example/o", next(pending)))

    async def callback(code, redirect_uri, signup_source=None):
        return user, "jwt-token", True

    async def no_region(user, ip):
        return None

    monkeypatch.setattr(auth, "handle_google_callback", callback)
    monkeypatch.setattr(auth, "maybe_infer_region", no_region)


def _sign_in_round_trip(client, state, next_path="/entities/person/x", headers=None):
    client.get("/api/auth/google/login", params={"next": next_path}, follow_redirects=False, headers=headers)
    done = client.get(
        "/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False, headers=headers
    )
    assert done.status_code == 302


def test_sign_in_events_form_one_anonymous_funnel_per_attempt(client, sink, app_state, monkeypatch):
    from thestill.web.analytics_request import login_flow_key

    user = app_state.auth_service.user_repository.save(User(email="new@example.com"))
    _stub_oauth(app_state, monkeypatch, user, ["state-1", "state-2"])

    _sign_in_round_trip(client, "state-1")
    _sign_in_round_trip(client, "state-2", next_path="/top")

    first_start, first_done, second_start, second_done = sink.captured
    assert [c["event"].name for c in sink.captured] == ["login_started", "login_completed"] * 2
    assert (first_start["event"].from_kind, first_done["event"].next_kind) == ("entity", "entity")
    assert second_done["event"].next_kind == "top"

    # Start and completion share one anonymous attempt id, never the account.
    assert first_start["visitor_key"] == first_done["visitor_key"] == login_flow_key("test" * 16, "state-1")
    assert second_start["visitor_key"] == second_done["visitor_key"] != first_start["visitor_key"]
    assert all(c["user"] is None for c in sink.captured)
    assert "state-1" not in first_start["visitor_key"]


def test_sign_in_events_respect_opt_out_and_gpc(client, sink, app_state, monkeypatch):
    opted_out = app_state.auth_service.user_repository.save(
        User(email="quiet@example.com", usage_analytics_enabled=False)
    )
    _stub_oauth(app_state, monkeypatch, opted_out, ["state-a", "state-b"])

    _sign_in_round_trip(client, "state-a")
    assert [c["event"].name for c in sink.captured] == ["login_started"]  # no completion for an opted-out reader

    sink.captured.clear()
    _sign_in_round_trip(client, "state-b", headers={"Sec-GPC": "1"})
    assert all(c["visitor_key"] is None and c["gpc"] is True for c in sink.captured)


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


def test_analytics_can_never_fail_a_sign_in(client, sink, app_state, monkeypatch):
    """The OAuth callback turns any exception into a failed sign-in, so the
    usage-event path inside it must be total, even for an odd user object."""
    _stub_oauth(app_state, monkeypatch, SimpleNamespace(email="odd@example.com"), ["state-x"])
    _sign_in_round_trip(client, "state-x")  # asserts the 302
    assert [c["event"].name for c in sink.captured] == ["login_started"]


def _start_sign_in(client, app_state, monkeypatch, state="state-1", next_path="/entities/person/x"):
    """A login that mints ``state``; the caller plays Google's reply."""
    user = app_state.auth_service.user_repository.save(User(email="new@example.com"))
    _stub_oauth(app_state, monkeypatch, user, [state])
    client.get("/api/auth/google/login", params={"next": next_path}, follow_redirects=False)


def test_cancel_on_google_ends_on_login_and_closes_the_attempt(client, sink, app_state, monkeypatch):
    from thestill.web.analytics_request import login_flow_key

    _start_sign_in(client, app_state, monkeypatch)
    back = client.get(
        "/api/auth/google/callback", params={"error": "access_denied", "state": "state-1"}, follow_redirects=False
    )

    # A page that explains it, not a raw validation error, and the retry
    # still returns to where sign-in started.
    assert back.status_code == 302
    assert back.headers["location"] == "/login?error=cancelled&next=%2Fentities%2Fperson%2Fx"
    assert AUTH_COOKIE_NAME not in back.cookies
    assert client.cookies.get("oauth_state") is None

    started, cancelled = sink.captured
    assert (started["event"].name, cancelled["event"].name) == ("login_started", "login_cancelled")
    assert cancelled["event"].reason == "access_denied"
    assert cancelled["visitor_key"] == started["visitor_key"] == login_flow_key("test" * 16, "state-1")
    assert cancelled["user"] is None


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"error": "temporarily_unavailable"}, "temporarily_unavailable"),
        ({"error": "<script>alert(1)</script>"}, "other"),
        ({}, "missing_code"),
    ],
)
def test_other_google_replies_are_bucketed_and_shown_as_failed(client, sink, app_state, monkeypatch, params, reason):
    _start_sign_in(client, app_state, monkeypatch)
    back = client.get("/api/auth/google/callback", params={**params, "state": "state-1"}, follow_redirects=False)

    assert back.status_code == 302
    assert back.headers["location"].startswith("/login?error=failed&")
    assert sink.captured[-1]["event"].name == "login_cancelled"
    assert sink.captured[-1]["event"].reason == reason
    assert "script" not in str(sink.captured[-1]["event"].properties())


def test_a_failed_exchange_ends_on_login_with_one_failed_event(client, sink, app_state, monkeypatch):
    _start_sign_in(client, app_state, monkeypatch)

    async def broken(code, redirect_uri, signup_source=None):
        raise RuntimeError("token endpoint said no: secret detail")

    monkeypatch.setattr(app_state.auth_service, "handle_google_callback", broken)
    back = client.get("/api/auth/google/callback", params={"code": "c", "state": "state-1"}, follow_redirects=False)

    assert back.status_code == 302
    assert back.headers["location"] == "/login?error=failed&next=%2Fentities%2Fperson%2Fx"
    assert "secret detail" not in back.text
    started, failed = sink.captured
    assert (failed["event"].name, failed["event"].reason) == ("login_failed", "error")
    assert failed["visitor_key"] == started["visitor_key"]


def test_a_state_mismatch_signs_nobody_in_and_ends_nothing(client, sink, app_state, monkeypatch):
    _start_sign_in(client, app_state, monkeypatch)
    back = client.get("/api/auth/google/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)

    assert back.status_code == 302
    assert back.headers["location"] == "/login?error=failed"
    assert AUTH_COOKIE_NAME not in back.cookies
    # The attempt this browser started is still open: cookies kept, no outcome.
    assert client.cookies.get("oauth_state") == "state-1"
    assert [c["event"].name for c in sink.captured] == ["login_started"]


@pytest.mark.parametrize(
    "stale_reply",
    [{"code": "c", "state": "state-a"}, {"error": "access_denied", "state": "state-a"}],
    ids=["older-tab-signs-in", "older-tab-cancels"],
)
def test_a_stale_tab_never_ends_the_newer_attempt(client, sink, app_state, monkeypatch, stale_reply):
    """Two sign-in tabs share one cookie jar: B replaces A's state and return
    path, so A's callback (sign-in or cancel) is stale and must leave B able
    to finish where B started."""
    from thestill.web.analytics_request import login_flow_key

    user = app_state.auth_service.user_repository.save(User(email="new@example.com"))
    _stub_oauth(app_state, monkeypatch, user, ["state-a", "state-b"])
    client.get("/api/auth/google/login", params={"next": "/entities/person/x"}, follow_redirects=False)
    client.get("/api/auth/google/login", params={"next": "/top"}, follow_redirects=False)

    stale = client.get("/api/auth/google/callback", params=stale_reply, follow_redirects=False)
    assert stale.headers["location"].startswith("/login?error=")
    assert "next=" not in stale.headers["location"]  # B's return path is not A's
    assert AUTH_COOKIE_NAME not in stale.cookies

    done = client.get("/api/auth/google/callback", params={"code": "c", "state": "state-b"}, follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == "/top"
    assert AUTH_COOKIE_NAME in done.cookies

    names = [c["event"].name for c in sink.captured]
    assert names == ["login_started", "login_started", "login_completed"]
    assert sink.captured[-1]["visitor_key"] == login_flow_key("test" * 16, "state-b")


@pytest.mark.parametrize(
    ("error", "notice"),
    [(AccountDisabledError("banned"), "account_disabled"), (SignupRefusedError("closed"), "signups_closed")],
)
def test_refusals_are_counted_as_failed_sign_ins(client, sink, app_state, monkeypatch, error, notice):
    _start_sign_in(client, app_state, monkeypatch)

    async def refuse(code, redirect_uri, signup_source=None):
        raise error

    monkeypatch.setattr(app_state.auth_service, "handle_google_callback", refuse)
    back = client.get("/api/auth/google/callback", params={"code": "c", "state": "state-1"}, follow_redirects=False)

    assert back.headers["location"] == f"/login?error={notice}&next=%2Fentities%2Fperson%2Fx"
    assert (sink.captured[-1]["event"].name, sink.captured[-1]["event"].reason) == ("login_failed", notice)


def test_a_reply_with_no_attempt_in_this_browser_sends_nothing(client, sink):
    back = client.get("/api/auth/google/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert back.status_code == 302
    assert back.headers["location"] == "/login?error=cancelled"
    assert sink.captured == []


# -- spec #103 Phase 2: per-page daily counter -------------------------------

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
EPISODE_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def counter(app_state):
    """A real counter over the app's SQLite database, with one public show
    (one summarised and one unsummarised episode) and one hidden show."""
    db = app_state.repository.db_path
    app_state.public_page_view_repository = SqlitePublicPageViewRepository(db)
    summary = Path(app_state.path_manager.summary_file("summarised_summary.md"))
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("## 1. The Gist\n\nTwo hosts argue about chips.\n", encoding="utf-8")
    now = datetime.now(timezone.utc)

    def episode(n, slug, summarised):
        return Episode(
            id=f"00000000-0000-0000-0000-00000000000{n}",
            external_id=f"guid-{n}",
            title=f"Episode {n}",
            description="",
            slug=slug,
            pub_date=now - timedelta(days=n),
            audio_url=f"https://example.com/{n}.mp3",
            summary_path="summarised_summary.md" if summarised else None,
        )

    for podcast_id, slug, episodes in (
        (PUBLIC_ID, "public-show", [episode(1, "summarised", True), episode(2, "no-summary", False)]),
        ("22222222-2222-2222-2222-222222222222", "hidden-show", [episode(3, "hidden-episode", True)]),
    ):
        app_state.repository.save(
            Podcast(
                id=podcast_id,
                rss_url=f"https://example.com/{slug}.xml",
                title=slug,
                description="",
                slug=slug,
                created_at=now,
                episodes=episodes,
            )
        )
    for n in (1, 3):
        app_state.repository.set_episode_summary_preview(f"00000000-0000-0000-0000-00000000000{n}", "Chips.")
    app_state.repository.set_podcast_public_pages("22222222-2222-2222-2222-222222222222", False)

    def rows():
        with connect(db) as conn:
            return {
                (r["kind"], r["object_id"]): r["views"]
                for r in conn.execute("SELECT kind, object_id, views FROM public_page_daily_views").fetchall()
            }

    return rows


def test_browser_views_go_on_the_page_counter(client, counter):
    for path in (
        "/podcasts/public-show",
        "/podcasts/public-show?ref=x",
        "/podcasts/public-show/episodes/summarised",
        "/",
        "/top",
    ):
        assert client.post("/api/public/pageview", json={"path": path}).status_code == 204
    assert counter() == {("podcast", PUBLIC_ID): 2, ("episode", EPISODE_ID): 1, ("landing", ""): 1, ("top", ""): 1}


def test_pages_that_are_not_counted(client, counter, app_state):
    for path in (
        "/podcasts/no-such-show",
        "/podcasts/hidden-show",  # public_pages = false
        "/podcasts/hidden-show/episodes/hidden-episode",
        "/podcasts/public-show/episodes/no-summary",  # the public site 404s it
        "/entities/person/nobody",
        "/share/abc",
        "/terms",
        "/inbox",
    ):
        client.post("/api/public/pageview", json={"path": path})
    client.post(
        "/api/public/pageview",
        json={"path": "/podcasts/public-show"},
        headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
    )
    _sign_in(client, app_state)
    client.post("/api/public/pageview", json={"path": "/podcasts/public-show"})
    assert counter() == {}


def test_a_failing_counter_never_fails_the_beacon(client, counter, app_state, sink, monkeypatch):
    def boom(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(app_state.public_page_view_repository, "increment", boom)
    assert client.post("/api/public/pageview", json={"path": "/podcasts/public-show"}).status_code == 204
    assert [c["event"].kind for c in sink.captured] == ["podcast"]


def test_the_counter_stores_no_visitor_data(counter, app_state):
    with connect(app_state.repository.db_path) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(public_page_daily_views)").fetchall()}
    assert columns == {"day", "kind", "object_id", "views"}


def test_entity_pages_count_under_the_stored_id_unless_hidden(client, counter, app_state):
    from thestill.models.entities import EntityRecord, EntityType

    repo = app_state.entity_repository
    repo.upsert_entity(EntityRecord(id="person:ada", type=EntityType.PERSON, canonical_name="Ada"))
    # Re-typed after its row existed (P31 gating): the URL type differs from the id prefix.
    repo.upsert_entity(EntityRecord(id="company:pais", type=EntityType.PERSON, canonical_name="Pais"))
    repo.upsert_entity(EntityRecord(id="person:hidden", type=EntityType.PERSON, canonical_name="Hidden"))
    repo.set_entity_public_hidden("person:hidden", True)
    clear_public_caches()

    for path in ("/entities/person/ada", "/entities/person/pais", "/entities/person/hidden"):
        client.post("/api/public/pageview", json={"path": path})
    assert counter() == {("entity", "person:ada"): 1, ("entity", "company:pais"): 1}
