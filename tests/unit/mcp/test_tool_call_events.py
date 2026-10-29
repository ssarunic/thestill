"""Remote MCP tool calls as usage events (spec #95 ``mcp_tool_call``)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.client import Client
from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from tests.unit.analytics_fakes import RecordingSink
from thestill.mcp.identity import STDIO
from thestill.mcp.registration import register_tools, tool_result_ok
from thestill.models.user import User
from thestill.web.mcp_http import McpHttpRuntime


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


@pytest.mark.parametrize(
    ("result", "ok"),
    [
        (_result('{"success": true, "items": []}'), True),
        (_result("A transcript, as plain text."), True),
        (_result('{"success": false, "error": "rate limited"}'), False),
        (_result('{"results": [], "error": "empty query"}'), False),
        (_result("{not json"), True),
        (_result("Input validation error: …", is_error=True), False),
        (CallToolResult(content=[]), True),
    ],
)
def test_tool_result_ok(result, ok):
    assert tool_result_ok(result) is ok


ECHO = Tool(name="echo", description="Echo", input_schema={"type": "object", "properties": {}})


@pytest.mark.anyio
async def test_every_call_is_observed_with_its_outcome():
    seen = []
    server = Server("events-test")

    async def list_tools(identity):
        return [ECHO]

    async def call_tool(name, arguments, identity):
        if name == "boom":
            raise RuntimeError("kaput")
        return [TextContent(type="text", text='{"success": true}')]

    register_tools(
        server,
        list_tools=list_tools,
        call_tool=call_tool,
        on_tool_called=lambda identity, name, ok: seen.append((identity, name, ok)),
    )
    async with Client(server) as client:
        await client.call_tool("echo", {})
        await client.call_tool("boom", {})
    assert seen == [(STDIO, "echo", True), (STDIO, "boom", False)]


class _ConnectorDays:
    """Records ``record_day`` calls; ``fail`` makes the next one raise."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def record_day(self, user_id, at):
        if self.fail:
            self.fail = False
            raise RuntimeError("database down")
        self.calls.append((user_id, at))
        return True


def _runtime(sink, connector_days=None) -> McpHttpRuntime:
    runtime = McpHttpRuntime.__new__(McpHttpRuntime)  # the counter needs no server
    runtime._analytics = sink
    runtime._connector_days = connector_days or _ConnectorDays()
    runtime._connector_hour = None
    runtime._connector_seen = set()
    return runtime


def test_remote_calls_by_a_user_are_counted():
    sink = RecordingSink()
    user = User(id="u-1", email="a@example.com")
    _runtime(sink)._count_tool_call(SimpleNamespace(user=user, gpc=False), "search_corpus", True)
    [call] = sink.captured
    assert (call["event"].tool, call["event"].ok, call["user"].id) == ("search_corpus", True, "u-1")


def test_global_privacy_control_travels_with_the_call():
    sink = RecordingSink()
    user = User(id="u-1", email="a@example.com")
    _runtime(sink)._count_tool_call(SimpleNamespace(user=user, gpc=True), "search_corpus", True)
    assert sink.captured[0]["gpc"] is True


def test_identity_reads_gpc_from_the_request_state():
    from thestill.mcp.identity import STATE_GPC, STATE_USER, current_mcp_identity

    user = User(id="u-1", email="a@example.com")
    ctx = SimpleNamespace(request=SimpleNamespace(scope={"state": {STATE_USER: user, STATE_GPC: True}}))
    assert current_mcp_identity(ctx).gpc is True
    ctx = SimpleNamespace(request=SimpleNamespace(scope={"state": {STATE_USER: user}}))
    assert current_mcp_identity(ctx).gpc is False


def test_calls_without_a_user_or_with_odd_names_are_skipped_quietly():
    sink = RecordingSink()
    runtime = _runtime(sink)
    runtime._count_tool_call(SimpleNamespace(user=None, gpc=False), "search_corpus", True)
    runtime._count_tool_call(SimpleNamespace(user=User(email="a@example.com"), gpc=False), "Not A Tool!", False)
    assert sink.captured == []
    _runtime(None)._count_tool_call(SimpleNamespace(user=User(email="a@example.com"), gpc=False), "x", True)


# ----- spec #111 connector days -------------------------------------------


def _at(hour, minute=0):
    return datetime(2026, 10, 29, hour, minute, tzinfo=timezone.utc)


def test_a_successful_call_records_the_day_once_per_hour(monkeypatch):
    clock = iter([_at(9, 5), _at(9, 40), _at(10, 1)])
    monkeypatch.setattr("thestill.web.mcp_http.now_utc", lambda: next(clock))
    days = _ConnectorDays()
    runtime = _runtime(None, days)  # no PostHog: connector days still count
    caller = SimpleNamespace(user=User(id="u-1", email="a@example.com"), gpc=False)
    for _ in range(3):
        runtime._count_tool_call(caller, "search_corpus", True)
    assert days.calls == [("u-1", _at(9, 5)), ("u-1", _at(10, 1))]


@pytest.mark.parametrize(
    ("user", "gpc", "ok"),
    [
        (User(id="u-1", email="a@example.com"), False, False),  # failed call
        (User(id="u-1", email="a@example.com"), True, True),  # Global Privacy Control
        (User(id="u-1", email="a@example.com", usage_analytics_enabled=False), False, True),  # opted out
        (None, False, True),  # no identity
    ],
)
def test_no_connector_day_without_a_consenting_successful_call(user, gpc, ok):
    days = _ConnectorDays()
    _runtime(RecordingSink(), days)._count_tool_call(SimpleNamespace(user=user, gpc=gpc), "search_corpus", ok)
    assert days.calls == []


def test_a_failing_write_never_fails_the_call_and_is_retried(monkeypatch):
    monkeypatch.setattr("thestill.web.mcp_http.now_utc", lambda: _at(9))
    days = _ConnectorDays()
    days.fail = True
    sink = RecordingSink()
    runtime = _runtime(sink, days)
    caller = SimpleNamespace(user=User(id="u-1", email="a@example.com"), gpc=False)
    runtime._count_tool_call(caller, "search_corpus", True)
    assert days.calls == [] and len(sink.captured) == 1  # the usage event still went out
    runtime._count_tool_call(caller, "search_corpus", True)
    assert days.calls == [("u-1", _at(9))]
