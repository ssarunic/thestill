"""Remote MCP tool calls as usage events (spec #95 ``mcp_tool_call``)."""

from __future__ import annotations

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


def _runtime(sink) -> McpHttpRuntime:
    runtime = McpHttpRuntime.__new__(McpHttpRuntime)  # the counter needs no server
    runtime._analytics = sink
    return runtime


def test_remote_calls_by_a_user_are_counted():
    sink = RecordingSink()
    user = User(id="u-1", email="a@example.com")
    _runtime(sink)._count_tool_call(SimpleNamespace(user=user), "search_corpus", True)
    [call] = sink.captured
    assert (call["event"].tool, call["event"].ok, call["user"].id) == ("search_corpus", True, "u-1")


def test_calls_without_a_user_or_with_odd_names_are_skipped_quietly():
    sink = RecordingSink()
    runtime = _runtime(sink)
    runtime._count_tool_call(SimpleNamespace(user=None), "search_corpus", True)
    runtime._count_tool_call(SimpleNamespace(user=User(email="a@example.com")), "Not A Tool!", False)
    assert sink.captured == []
    _runtime(None)._count_tool_call(SimpleNamespace(user=User(email="a@example.com")), "x", True)
