"""The https address of an http enclosure (``services/audio_address``)."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest
import requests

from thestill.services import audio_address
from thestill.services.audio_address import AudioAddressResolver
from thestill.utils.url_guard import UnsafeDestinationError


class FakeResponse:
    def __init__(self, status: int, location: Optional[str] = None) -> None:
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeHost:
    """Answers each URL from a table and records what it was asked."""

    def __init__(self, table: Dict[str, Tuple[int, Optional[str]]]) -> None:
        self.table = table
        self.calls: List[Tuple[str, dict]] = []
        self.responses: List[FakeResponse] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs))
        status, location = self.table.get(url, (404, None))
        response = FakeResponse(status, location)
        self.responses.append(response)
        return response


@pytest.fixture(autouse=True)
def public_hosts(monkeypatch):
    """Every host is public unless a test says otherwise (no DNS in tests)."""
    monkeypatch.setattr(audio_address, "validate_public_url", lambda url: None)


ENCLOSURE = "http://open.example.org/redir/proto/http/vpid/abc.mp3"
# What the BBC's chain does: an http Location from the first hop, then https.
CHAIN = {
    "https://open.example.org/redir/proto/http/vpid/abc.mp3": (302, "http://cdn.example.net/ak/abc.mp3?t=1"),
    "https://cdn.example.net/ak/abc.mp3?t=1": (302, "/v1/download/abc"),
    "https://cdn.example.net/v1/download/abc": (302, "https://cdn.example.net/v1/variant/abc.mp3?Signature=s"),
    "https://cdn.example.net/v1/variant/abc.mp3?Signature=s": (206, None),
}


def test_an_https_enclosure_is_returned_as_it_is_without_a_request():
    host = FakeHost({})
    resolver = AudioAddressResolver(get_fn=host.get)
    assert resolver.playable_url("https://cdn.example.net/a.mp3") == "https://cdn.example.net/a.mp3"
    assert host.calls == []


def test_follows_every_hop_over_https_and_returns_the_file_address():
    host = FakeHost(CHAIN)
    resolver = AudioAddressResolver(get_fn=host.get)

    assert resolver.playable_url(ENCLOSURE) == "https://cdn.example.net/v1/variant/abc.mp3?Signature=s"
    # http Locations are retried over https; a relative one is resolved.
    assert [url for url, _ in host.calls] == list(CHAIN)
    for _, kwargs in host.calls:
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs["headers"] == {"User-Agent": "Thestill/1.0", "Range": "bytes=0-0"}
        assert kwargs["timeout"] == audio_address.TIMEOUT_S
    assert all(response.closed for response in host.responses)


def test_an_address_without_an_expiry_is_reused_for_five_minutes_then_resolved_again():
    host = FakeHost(CHAIN)
    now = [1000.0]
    resolver = AudioAddressResolver(get_fn=host.get, clock=lambda: now[0])

    first = resolver.playable_url(ENCLOSURE)
    now[0] += 299
    assert resolver.playable_url(ENCLOSURE) == first
    assert len(host.calls) == len(CHAIN)

    now[0] += 2
    assert resolver.playable_url(ENCLOSURE) == first
    assert len(host.calls) == 2 * len(CHAIN)


def _signed_chain(expires: int) -> Dict[str, Tuple[int, Optional[str]]]:
    final = f"https://cdn.example.net/v1/variant/abc.mp3?Expires={expires}&Signature=s"
    return {"https://open.example.org/redir/proto/http/vpid/abc.mp3": (302, final), final: (206, None)}


def test_a_signed_address_is_kept_until_shortly_before_it_expires():
    now = [1_000_000.0]
    host = FakeHost(_signed_chain(expires=1_000_000 + 2 * 3600))
    resolver = AudioAddressResolver(get_fn=host.get, clock=lambda: now[0])

    first = resolver.playable_url(ENCLOSURE)
    # A long play asks again on every seek and must keep the same copy.
    now[0] += 2 * 3600 - audio_address.EXPIRY_MARGIN_S - 1
    assert resolver.playable_url(ENCLOSURE) == first
    assert len(host.calls) == 2

    now[0] += 2
    resolver.playable_url(ENCLOSURE)
    assert len(host.calls) == 4


def test_a_far_expiry_is_capped_and_a_near_one_is_not_remembered():
    now = [1_000_000.0]
    far = FakeHost(_signed_chain(expires=1_000_000 + 48 * 3600))
    resolver = AudioAddressResolver(get_fn=far.get, clock=lambda: now[0])
    resolver.playable_url(ENCLOSURE)
    now[0] += audio_address.MAX_TTL_S + 1
    resolver.playable_url(ENCLOSURE)
    assert len(far.calls) == 4

    near = FakeHost(_signed_chain(expires=1_000_000 + 60))
    resolver = AudioAddressResolver(get_fn=near.get, clock=lambda: 1_000_000.0)
    assert resolver.playable_url(ENCLOSURE) is not None
    resolver.playable_url(ENCLOSURE)
    assert len(near.calls) == 4


def test_each_listener_gets_and_keeps_their_own_address():
    host = FakeHost(CHAIN)
    resolver = AudioAddressResolver(get_fn=host.get, clock=lambda: 1000.0)
    resolver.playable_url(ENCLOSURE, listener="u1")
    resolver.playable_url(ENCLOSURE, listener="u2")
    resolver.playable_url(ENCLOSURE, listener="u1")
    # Two listeners, two walks of the chain: the publisher sees each of them.
    assert len(host.calls) == 2 * len(CHAIN)


def test_a_file_that_refuses_https_gives_none_and_is_not_remembered():
    table = dict(CHAIN)
    table["https://cdn.example.net/v1/variant/abc.mp3?Signature=s"] = (403, None)
    host = FakeHost(table)
    resolver = AudioAddressResolver(get_fn=host.get)

    assert resolver.playable_url(ENCLOSURE) is None
    assert resolver.playable_url(ENCLOSURE) is None
    assert len(host.calls) == 2 * len(CHAIN)


def test_a_redirect_loop_stops_at_the_hop_limit():
    host = FakeHost({"https://loop.example.org/a.mp3": (302, "http://loop.example.org/a.mp3")})
    resolver = AudioAddressResolver(get_fn=host.get)
    assert resolver.playable_url("http://loop.example.org/a.mp3") is None
    assert len(host.calls) == audio_address.MAX_HOPS + 1


def test_a_hop_to_an_internal_address_is_refused_before_it_is_requested(monkeypatch):
    def guard(url: str) -> None:
        if "169.254.169.254" in url:
            raise UnsafeDestinationError("internal address")

    monkeypatch.setattr(audio_address, "validate_public_url", guard)
    host = FakeHost({"https://open.example.org/a.mp3": (302, "http://169.254.169.254/latest/meta-data/")})
    resolver = AudioAddressResolver(get_fn=host.get)

    assert resolver.playable_url("http://open.example.org/a.mp3") is None
    assert [url for url, _ in host.calls] == ["https://open.example.org/a.mp3"]


def test_a_network_failure_gives_none():
    def failing(url: str, **kwargs):
        raise requests.ConnectionError("no route")

    resolver = AudioAddressResolver(get_fn=failing)
    assert resolver.playable_url(ENCLOSURE) is None


def test_other_schemes_are_refused():
    resolver = AudioAddressResolver(get_fn=FakeHost({}).get)
    assert resolver.playable_url("file:///etc/passwd") is None
    assert resolver.playable_url("ftp://example.org/a.mp3") is None
