"""Visitor key, GPC and the capture helper (spec #95 §3.5)."""

from datetime import date
from types import SimpleNamespace

from starlette.requests import Request

from tests.unit.analytics_fakes import RecordingSink
from thestill.services.analytics_sink import LoginStarted
from thestill.web.analytics_request import capture_event, daily_visitor_key, login_flow_key, request_gpc, visitor_key


def _request(headers=None, ip="203.0.113.7"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "client": (ip, 1234)})


def test_visitor_key_rotates_daily_and_hides_the_ip():
    a = daily_visitor_key("secret", "203.0.113.7", "UA", date(2026, 10, 16))
    b = daily_visitor_key("secret", "203.0.113.7", "UA", date(2026, 10, 17))
    assert a != b
    assert len(a) == 32
    assert "203" not in a
    assert a == daily_visitor_key("secret", "203.0.113.7", "UA", date(2026, 10, 16))
    assert a != daily_visitor_key("other", "203.0.113.7", "UA", date(2026, 10, 16))


def test_gpc_means_no_visitor_key():
    assert request_gpc(_request({"Sec-GPC": "1"})) is True
    assert visitor_key(_request({"Sec-GPC": "1"}), "s") is None
    assert request_gpc(_request()) is False
    assert visitor_key(_request({"User-Agent": "UA"}), "s", SimpleNamespace(trusted_proxies=[]))


def _state(sink):
    return SimpleNamespace(analytics_sink=sink, config=SimpleNamespace(jwt_secret_key="s", trusted_proxies=[]))


def test_capture_event_anonymous_gets_a_visitor_key():
    sink = RecordingSink()
    capture_event(_state(sink), _request({"User-Agent": "UA"}), LoginStarted(from_kind="episode"))
    [call] = sink.captured
    assert call["visitor_key"] and call["user"] is None and call["gpc"] is False


def test_capture_event_passes_gpc_and_skips_when_off():
    sink = RecordingSink()
    capture_event(_state(sink), _request({"Sec-GPC": "1"}), LoginStarted(from_kind="none"))
    assert sink.captured[0]["gpc"] is True and sink.captured[0]["visitor_key"] is None

    capture_event(_state(None), _request(), LoginStarted(from_kind="none"))  # unwired: no-op


def test_capture_event_never_raises():
    class Exploding(RecordingSink):
        def capture(self, *a, **k):
            raise RuntimeError("boom")

    capture_event(_state(Exploding()), _request(), LoginStarted(from_kind="none"))


def test_login_flow_key_is_stable_per_attempt_and_hides_the_state():
    a = login_flow_key("secret", "oauth-state-1")
    assert a == login_flow_key("secret", "oauth-state-1")
    assert a != login_flow_key("secret", "oauth-state-2")
    assert a != login_flow_key("other", "oauth-state-1")
    assert "oauth-state" not in a and len(a) == 32


def test_capture_event_uses_an_explicit_anonymous_key_but_not_under_gpc():
    sink = RecordingSink()
    capture_event(_state(sink), _request(), LoginStarted(from_kind="none"), anonymous_key="flow-1")
    capture_event(_state(sink), _request({"Sec-GPC": "1"}), LoginStarted(from_kind="none"), anonymous_key="flow-1")
    assert [c["visitor_key"] for c in sink.captured] == ["flow-1", None]
