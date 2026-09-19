"""The mcp 2.x registration adapter (``thestill/mcp/registration.py``).

mcp 1.x's ``@server.call_tool()`` decorator validated arguments, wrapped
plain return values and turned exceptions into error results. 2.x does none
of that, so the adapter does; these tests pin each behaviour through a real
in-memory client/server session rather than by calling the adapter directly.
"""

from __future__ import annotations

import pytest
from mcp.client import Client
from mcp.server import Server
from mcp.shared.exceptions import MCPError
from mcp.types import Resource, TextContent, Tool

from thestill.mcp.identity import STDIO
from thestill.mcp.registration import register_resources, register_tools


@pytest.fixture
def anyio_backend():
    return "asyncio"


ECHO = Tool(
    name="echo",
    description="Echo a message",
    input_schema={"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
)


def _tool_server(*, listed=(ECHO,)):
    calls = []
    server = Server("registration-test")

    async def list_tools(identity):
        return list(listed)

    async def call_tool(name, arguments, identity):
        calls.append((name, arguments, identity))
        if name == "boom":
            raise RuntimeError("kaput")
        return [TextContent(type="text", text=f"{name}:{arguments.get('message')}")]

    register_tools(server, list_tools=list_tools, call_tool=call_tool)
    return server, calls


class TestTools:
    @pytest.mark.anyio
    async def test_lists_tools_and_advertises_the_capability(self):
        server, _ = _tool_server()
        async with Client(server) as client:
            assert client.server_capabilities.tools is not None
            assert [t.name for t in (await client.list_tools()).tools] == ["echo"]

    @pytest.mark.anyio
    async def test_plain_content_list_is_wrapped_and_identity_is_stdio_without_a_request(self):
        server, calls = _tool_server()
        async with Client(server) as client:
            result = await client.call_tool("echo", {"message": "hi"})
        assert result.is_error is not True
        assert result.content[0].text == "echo:hi"
        assert calls == [("echo", {"message": "hi"}, STDIO)]

    @pytest.mark.anyio
    async def test_arguments_failing_the_schema_never_reach_the_handler(self):
        server, calls = _tool_server()
        async with Client(server) as client:
            result = await client.call_tool("echo", {"message": 42})
        assert result.is_error is True
        assert "Input validation error" in result.content[0].text
        assert calls == []

    @pytest.mark.anyio
    async def test_missing_arguments_are_validated_as_an_empty_object(self):
        server, calls = _tool_server()
        async with Client(server) as client:
            result = await client.call_tool("echo")
        assert result.is_error is True and "'message' is a required property" in result.content[0].text
        assert calls == []

    @pytest.mark.anyio
    async def test_unlisted_tool_skips_validation_so_the_handler_can_refuse_it(self):
        """Over the remote connector a tool outside the token's scopes is not
        listed; the call must still reach the handler, whose scope check
        produces the refusal that names the missing scope."""
        server, calls = _tool_server(listed=())
        async with Client(server) as client:
            result = await client.call_tool("echo", {"message": 42})
        assert result.is_error is not True
        assert [c[0] for c in calls] == ["echo"]

    @pytest.mark.anyio
    async def test_handler_exception_becomes_an_error_result_not_a_protocol_error(self):
        server, _ = _tool_server(listed=())
        async with Client(server) as client:
            result = await client.call_tool("boom", {})
        assert result.is_error is True
        assert result.content[0].text == "kaput"


class TestResources:
    def _server(self):
        server = Server("registration-test")

        async def list_resources(identity):
            return [Resource(uri="thestill://podcasts/{podcast_id}", name="Podcast", mime_type="application/json")]

        async def read_resource(uri, identity):
            assert isinstance(uri, str) and identity is STDIO
            if uri.endswith("/missing"):
                raise ValueError(f"Podcast not found: {uri}")
            if uri.endswith("/bug"):
                raise RuntimeError("secret internals")
            return f"body of {uri}"

        register_resources(server, list_resources=list_resources, read_resource=read_resource)
        return server

    @pytest.mark.anyio
    async def test_list_and_read(self):
        async with Client(self._server()) as client:
            assert client.server_capabilities.resources is not None
            listed = (await client.list_resources()).resources
            assert [r.name for r in listed] == ["Podcast"]
            read = await client.read_resource("thestill://podcasts/abc")
        assert read.contents[0].text == "body of thestill://podcasts/abc"
        assert read.contents[0].uri == "thestill://podcasts/abc"

    @pytest.mark.anyio
    async def test_caller_facing_failure_keeps_its_message(self):
        async with Client(self._server()) as client:
            with pytest.raises(MCPError) as excinfo:
                await client.read_resource("thestill://podcasts/missing")
        assert "Podcast not found: thestill://podcasts/missing" in str(excinfo.value)

    @pytest.mark.anyio
    async def test_unexpected_failure_is_opaque(self):
        async with Client(self._server()) as client:
            with pytest.raises(MCPError) as excinfo:
                await client.read_resource("thestill://podcasts/bug")
        assert "secret internals" not in str(excinfo.value)
