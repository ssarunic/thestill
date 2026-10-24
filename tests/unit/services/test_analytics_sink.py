"""Server-side usage events (spec #95 Layer B)."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from thestill.models.user import User
from thestill.services.analytics_sink import (
    Follow,
    LoginStarted,
    McpToolCall,
    NullSink,
    PostHogSink,
    PublicPageView,
    Search,
    follow_source,
    make_analytics_sink,
)


class FakeClient:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.shut = False

    def capture(self, event, **kwargs):
        if self.fail:
            raise RuntimeError("network down")
        self.calls.append((event, kwargs))

    def shutdown(self):
        self.shut = True


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def sink(client):
    return PostHogSink("phc_key", "https://eu.i.posthog.com", client=client)


def _user(**overrides):
    return User(id="u-1", email="a@example.com", **overrides)


def test_identified_event_uses_the_user_id_and_is_admin_only(sink, client):
    sink.capture(Search(result_count=3, mode="hybrid"), user=_user(is_admin=True))
    [(name, kwargs)] = client.calls
    assert name == "search"
    assert kwargs["distinct_id"] == "u-1"
    assert kwargs["properties"] == {"result_count": 3, "mode": "hybrid", "app": "thestill", "$set": {"is_admin": True}}


def test_opted_out_user_and_gpc_send_nothing(sink, client):
    sink.capture(Search(result_count=1, mode="lexical"), user=_user(usage_analytics_enabled=False))
    sink.capture(Search(result_count=1, mode="lexical"), user=_user(), gpc=True)
    assert client.calls == []


def test_anonymous_event_never_creates_a_person(sink, client):
    sink.capture(PublicPageView(kind="episode", referrer_host="news.ycombinator.com"), visitor_key="v" * 32)
    sink.capture(LoginStarted(from_kind="none"))
    (_, first), (_, second) = client.calls
    assert first["distinct_id"] == "v" * 32
    assert first["properties"]["$process_person_profile"] is False
    assert first["properties"]["app"] == "thestill"
    assert second["distinct_id"] and second["distinct_id"] != first["distinct_id"]  # one-off id
    assert second["properties"]["$process_person_profile"] is False


def test_capture_never_raises(caplog):
    sink = PostHogSink("phc_key", "https://x", client=FakeClient(fail=True))
    sink.capture(Search(result_count=0, mode="lexical"), user=_user())  # no exception


def test_shutdown_flushes(sink, client):
    sink.shutdown()
    assert client.shut is True


def test_events_reject_unknown_props_and_bad_values():
    with pytest.raises(ValidationError):
        Search(result_count=1, mode="hybrid", query="secret words")
    with pytest.raises(ValidationError):
        PublicPageView(kind="inbox")
    with pytest.raises(ValidationError):
        McpToolCall(tool="Robert'); DROP TABLE", ok=True)
    with pytest.raises(ValidationError):
        Search(result_count=-1, mode="hybrid")
    with pytest.raises(ValidationError):
        Follow(source="newsletter")


def test_follow_source_maps_anything_unknown_to_other():
    assert follow_source("top_podcasts") == "top_podcasts"
    assert follow_source("episode_prompt") == "episode_prompt"
    assert follow_source(None) == "other"
    assert follow_source("TOP_PODCASTS") == "other"


def test_null_sink_is_inert():
    sink = NullSink()
    assert sink.enabled is False and sink.browser_key is None
    sink.capture(Search(result_count=1, mode="hybrid"), user=_user())
    sink.shutdown()


@pytest.mark.parametrize(
    ("multi_user", "enabled", "key", "expected"),
    [
        (True, True, "phc_x", True),
        (False, True, "phc_x", False),
        (True, False, "phc_x", False),
        (True, True, "", False),
    ],
)
def test_make_analytics_sink(monkeypatch, multi_user, enabled, key, expected):
    import thestill.services.analytics_sink as module

    monkeypatch.setattr(module, "PostHogSink", lambda k, h: SimpleNamespace(enabled=True, browser_key=k))
    config = SimpleNamespace(
        multi_user=multi_user, analytics_events_enabled=enabled, posthog_project_key=key, posthog_host="https://x"
    )
    assert make_analytics_sink(config).enabled is expected
