"""Client ID Metadata Document fetch fences and cache (spec #109)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from thestill.repositories.sqlite_oauth_repository import SqliteOAuthRepository
from thestill.repositories.sqlite_podcast_repository import SqlitePodcastRepository
from thestill.services import oauth_cimd
from thestill.services.oauth_cimd import CimdError, CimdResolver, FetchedDocument, validate_document

URL = "https://claude.ai/oauth/claude-code-client-metadata"


def _doc(**overrides):
    base = {"client_id": URL, "client_name": "Claude Code", "redirect_uris": ["http://localhost/callback"]}
    base.update(overrides)
    return base


def test_validate_document_accepts_doc_without_scope_and_defaults_public():
    got = validate_document(URL, _doc())
    assert got["token_endpoint_auth_method"] == "none" and "scope" not in got


@pytest.mark.parametrize(
    "doc",
    [
        _doc(client_id="https://other.example/doc"),
        _doc(redirect_uris=[]),
        _doc(redirect_uris="http://localhost/callback"),
        _doc(token_endpoint_auth_method="client_secret_basic"),
        _doc(redirect_uris=["javascript:alert(1)//"]),
        _doc(redirect_uris=["http://evil.example/callback"]),
    ],
)
def test_validate_document_refuses(doc):
    with pytest.raises(CimdError):
        validate_document(URL, doc)


@pytest.mark.parametrize(
    "url",
    [
        "http://claude.ai/doc",
        "https://user:pw@claude.ai/doc",
        "https://127.0.0.1/doc",
        "https://localhost/doc",
        "https://10.0.0.5/doc",
        "https://169.254.169.254/latest/meta-data",
        "https://claude.ai:abc/doc",
        "https://a..b/doc",
    ],
)
def test_fetch_refuses_unsafe_urls_before_any_request(url, monkeypatch):
    monkeypatch.setattr(oauth_cimd.httpx, "AsyncClient", lambda *a, **k: pytest.fail("must not fetch"))
    with pytest.raises(CimdError):
        oauth_cimd.fetch_document(url)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "t.db")
    SqlitePodcastRepository(db_path=db)
    return SqliteOAuthRepository(db_path=db)


def test_resolver_caches_for_max_age_capped(repo):
    clock = Clock()
    calls = []

    def fetch(url):
        calls.append(url)
        return FetchedDocument(_doc(), timedelta(days=7))

    resolver = CimdResolver(repo, fetch=fetch, clock=clock)
    first = resolver.resolve(URL)
    assert first.client_name == "Claude Code" and first.fresh_until == clock.now + timedelta(hours=24)
    clock.now += timedelta(hours=23)
    resolver.resolve(URL)
    assert len(calls) == 1
    clock.now += timedelta(hours=2)
    resolver.resolve(URL)
    assert len(calls) == 2


def test_resolver_keeps_last_good_copy_on_failure(repo):
    clock = Clock()
    state = {"fail": False}

    def fetch(url):
        if state["fail"]:
            raise CimdError("down")
        return FetchedDocument(_doc(), None)

    resolver = CimdResolver(repo, fetch=fetch, clock=clock)
    assert resolver.resolve(URL) is not None
    state["fail"] = True
    clock.now += timedelta(hours=2)
    assert resolver.resolve(URL).client_name == "Claude Code"


def test_resolver_unknown_and_failing_is_none(repo):
    def fetch(url):
        raise CimdError("down")

    assert CimdResolver(repo, fetch=fetch, clock=Clock()).resolve(URL) is None


def test_resolver_refuses_invalid_document(repo):
    resolver = CimdResolver(repo, fetch=lambda url: FetchedDocument(_doc(client_id="https://x"), None), clock=Clock())
    assert resolver.resolve(URL) is None


def test_resolver_backs_off_after_a_failure(repo):
    calls = []

    def fetch(url):
        calls.append(url)
        raise CimdError("down")

    resolver = CimdResolver(repo, fetch=fetch, clock=Clock())
    assert resolver.resolve(URL) is None
    assert resolver.resolve(URL) is None
    assert len(calls) == 1


def test_fetch_deadline_covers_the_whole_request(monkeypatch):
    """Codex P1 on #442: a server dripping response headers must not hold a
    lookup past the deadline. Stand in for a hanging DNS-to-headers phase."""
    import anyio

    async def public(*_a, **_k):
        return [(None, None, None, None, ("93.184.216.34", 443))]

    class Hanging:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            await anyio.sleep(60)

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(oauth_cimd.anyio, "getaddrinfo", public)
    monkeypatch.setattr(oauth_cimd.httpx, "AsyncClient", Hanging)
    monkeypatch.setattr(oauth_cimd, "DEADLINE_SECONDS", 0.2)
    with pytest.raises(CimdError, match="took too long"):
        oauth_cimd.fetch_document(URL)
