"""``scripts/check_connector.py`` — the scripted pre-submission check.

Run against an in-memory server that lists the real tool names (with the
real annotations) and answers with canned JSON, so the test pins which
tools the script calls, with what, and how it reports.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from mcp.client import Client
from mcp.server import Server
from mcp.types import TextContent, Tool

from thestill.mcp.annotations import annotate
from thestill.mcp.registration import register_tools
from thestill.mcp.scopes import registered_tools, scope_for_tool

_SCRIPT = Path(__file__).parents[3] / "scripts" / "check_connector.py"
_spec = importlib.util.spec_from_file_location("check_connector", _SCRIPT)
check_connector = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_connector  # dataclasses look the module up by name
_spec.loader.exec_module(check_connector)

READER_TOOLS = sorted(name for name in registered_tools() if scope_for_tool(name) in ("read", "follows"))
SEARCH_HIT = {"podcast_id": "pod-1", "episode_id": "ep-1", "start_ms": 62000, "speaker": "Ada Lovelace"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _server(names, *, failing=(), annotated=True, answers=None):
    calls = []
    tools = [Tool(name=name, description="d", input_schema={"type": "object"}) for name in names]
    if annotated:
        tools = annotate(tools)
    server = Server("check-connector-test")

    async def list_tools(identity):
        return tools

    async def call_tool(name, arguments, identity):
        calls.append((name, arguments))
        if name in failing:
            return [TextContent(type="text", text=json.dumps({"success": False, "error": "nope"}))]
        payload = {"success": True}
        if name == "search_corpus":
            payload["results"] = [SEARCH_HIT]
        if name == "add_podcast":
            payload["podcast"] = {"id": "pod-9", "slug": "new-show"}
        payload.update((answers or {}).get(name, {}))
        return [TextContent(type="text", text=json.dumps(payload))]

    register_tools(server, list_tools=list_tools, call_tool=call_tool)
    return server, calls


async def _run(server, **options):
    async with Client(server) as client:
        return await check_connector.run_checks(client, check_connector.Options(**options))


def _status(report):
    return {o.tool: o.status for o in report.outcomes}


@pytest.mark.anyio
async def test_read_run_calls_every_read_tool_and_no_write_tool():
    server, calls = _server(READER_TOOLS)
    report = await _run(server)
    status = _status(report)
    assert not report.failed
    called = {name for name, _ in calls}
    assert called == {name for name in READER_TOOLS if scope_for_tool(name) == "read"}
    assert {name for name, s in status.items() if s == "skipped"} == check_connector.WRITE_TOOLS
    assert status["tools/list"] == "ok"


@pytest.mark.anyio
async def test_later_calls_use_the_search_hit():
    server, calls = _server(READER_TOOLS)
    await _run(server, query="rates", entity="Nvidia")
    by_name = dict(calls)
    assert by_name["search_corpus"] == {"query": "rates", "limit": 5}
    assert by_name["get_summary"] == {"podcast_id": "pod-1", "episode_id": "ep-1"}
    assert by_name["get_episode_clip"] == {"episode_id": "ep-1", "start_ms": 62000}
    assert by_name["list_quotes_by"] == {"speaker": "Ada Lovelace", "limit": 5}
    assert by_name["list_episodes_by_entity"] == {"has_entity": ["Nvidia"], "limit": 5}


@pytest.mark.anyio
async def test_write_run_follows_then_unfollows_the_same_show():
    server, calls = _server(READER_TOOLS)
    report = await _run(server, write=True, follow_url="https://example.com/feed.xml")
    assert not report.failed
    by_name = dict(calls)
    assert by_name["send_to_inbox"] == {"episode_id": "ep-1"}
    assert by_name["add_podcast"] == {"url": "https://example.com/feed.xml"}
    assert by_name["remove_podcast"] == {"podcast_id": "pod-9"}
    assert "import_episode" not in by_name
    assert _status(report)["import_episode"] == "skipped"


@pytest.mark.anyio
async def test_a_failing_tool_is_reported_and_the_rest_still_run():
    server, calls = _server(READER_TOOLS, failing={"get_summary"})
    report = await _run(server)
    assert [(o.tool, o.detail) for o in report.failed] == [("get_summary", "nope")]
    assert "get_transcript" in dict(calls)


@pytest.mark.anyio
async def test_empty_search_fails_the_tools_that_depend_on_it():
    server, _ = _server(READER_TOOLS, answers={"search_corpus": {"results": []}})
    report = await _run(server)
    assert {o.tool for o in report.failed} == {
        "list_episodes",
        "get_summary",
        "get_transcript",
        "get_episode_clip",
        "list_quotes_by",
    }


@pytest.mark.anyio
async def test_tools_without_hints_fail_the_listing_check():
    server, _ = _server(["get_status"], annotated=False)
    report = await _run(server)
    assert _status(report)["tools/list"] == "FAIL"


@pytest.mark.anyio
async def test_operator_tools_are_never_called_and_unknown_tools_fail():
    server, calls = _server([*READER_TOOLS, "refresh_feeds", "brand_new_tool"])
    report = await _run(server)
    status = _status(report)
    assert status["refresh_feeds"] == "skipped"
    assert status["brand_new_tool"] == "FAIL"
    assert "refresh_feeds" not in dict(calls)


def test_every_registered_tool_is_covered_by_the_script():
    """A tool added to the connector must be called or deliberately skipped."""
    source = _SCRIPT.read_text(encoding="utf-8")
    assert all(f'"{name}"' in source for name in registered_tools())


def test_missing_url_exits_without_connecting(monkeypatch, capsys):
    monkeypatch.delenv(check_connector.URL_ENV, raising=False)
    assert check_connector.main([]) == 2
    assert check_connector.URL_ENV in capsys.readouterr().err


SECRET_URL = "https://thestill.example/mcp/REVIEW_TEST_SECRET"


class _RaisingClient:
    """A client whose tool calls fail with the connector URL in the error."""

    async def list_tools(self):
        tools = annotate([Tool(name="get_status", description="d", input_schema={"type": "object"})])
        return type("Listed", (), {"tools": tools})()

    async def call_tool(self, name, arguments):
        raise RuntimeError(f"Redirect to http://thestill.example/mcp/REVIEW_TEST_SECRET/ for {name}")


@pytest.mark.anyio
async def test_a_tool_error_never_prints_the_connector_url():
    report = await check_connector.run_checks(_RaisingClient(), check_connector.Options(), url=SECRET_URL)
    rendered = report.render()
    assert _status(report)["get_status"] == "FAIL"
    assert "REVIEW_TEST_SECRET" not in rendered
    assert "RuntimeError" in rendered


def test_transport_warnings_never_print_the_connector_url(monkeypatch, capsys):
    import logging

    monkeypatch.setenv(check_connector.URL_ENV, SECRET_URL)

    async def fake_main(options, url):
        logging.getLogger("mcp.client.streamable_http").warning("Redirect to %s/", url)
        return check_connector.Report()

    monkeypatch.setattr(check_connector, "_main", fake_main)
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert check_connector.main([]) == 0
    finally:
        for handler in root.handlers[:]:
            if handler not in before:
                root.removeHandler(handler)
    err = capsys.readouterr().err
    assert "REVIEW_TEST_SECRET" not in err
    assert "Redirect to" in err


@pytest.mark.anyio
async def test_a_long_tool_error_is_redacted_before_it_is_cut_short():
    class LongError(_RaisingClient):
        async def call_tool(self, name, arguments):
            raise RuntimeError("x" * 140 + SECRET_URL)

    report = await check_connector.run_checks(LongError(), check_connector.Options(), url=SECRET_URL)
    assert "REVIEW_TEST" not in report.render()
