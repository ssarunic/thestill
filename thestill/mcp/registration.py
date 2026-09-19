# Copyright 2025-2026 Thestill
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Handler registration against the mcp 2.x low-level ``Server``.

mcp 1.x registered handlers with ``@server.list_tools()`` /
``@server.call_tool()`` decorators that also wrapped plain return values,
validated tool arguments and turned exceptions into error results. 2.x
dropped the decorators: handlers are registered per JSON-RPC method,
receive the request context explicitly and return full result models.

This module is the one place that knows that shape. ``tools.py`` and
``resources.py`` keep handing over simple callables
(``call_tool(name, arguments, identity) -> list[TextContent]``) and the
adapters here supply what the 1.x decorators used to: identity resolution
from the context, argument validation, result wrapping, error results.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List

import jsonschema
import structlog
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError

from .identity import McpIdentity, NotAuthenticatedError, current_mcp_identity

logger = structlog.get_logger(__name__)

ListToolsFn = Callable[[McpIdentity], Awaitable[List[types.Tool]]]
CallToolFn = Callable[[str, Dict[str, Any], McpIdentity], Awaitable[List[types.TextContent]]]
ListResourcesFn = Callable[[McpIdentity], Awaitable[List[types.Resource]]]
ReadResourceFn = Callable[[str, McpIdentity], Awaitable[str]]


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def register_tools(server: Server, *, list_tools: ListToolsFn, call_tool: CallToolFn) -> None:
    """Serve ``tools/list`` and ``tools/call`` from the given callables."""

    async def on_list_tools(
        ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=await list_tools(current_mcp_identity(ctx)))

    async def on_call_tool(ctx: ServerRequestContext, params: types.CallToolRequestParams) -> types.CallToolResult:
        identity = current_mcp_identity(ctx)
        arguments = params.arguments or {}

        # Validate against the schema the caller was shown. A tool the
        # caller cannot see has no schema here and goes straight to the
        # handler, whose scope check produces the real refusal.
        tool = next((t for t in await list_tools(identity) if t.name == params.name), None)
        if tool is not None:
            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as exc:
                return _error_result(f"Input validation error: {exc.message}")

        try:
            content = await call_tool(params.name, arguments, identity)
        except Exception as exc:  # pylint: disable=broad-except
            # A tool failure is a result the model can read and react to,
            # not a protocol error.
            logger.error("mcp_tool_call_failed", tool=params.name, error=str(exc), exc_info=True)
            return _error_result(str(exc))
        return types.CallToolResult(content=list(content))

    server.add_request_handler("tools/list", types.PaginatedRequestParams, on_list_tools)
    server.add_request_handler("tools/call", types.CallToolRequestParams, on_call_tool)


def register_resources(server: Server, *, list_resources: ListResourcesFn, read_resource: ReadResourceFn) -> None:
    """Serve ``resources/list`` and ``resources/read`` from the given callables."""

    async def on_list_resources(
        ctx: ServerRequestContext, params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(resources=await list_resources(current_mcp_identity(ctx)))

    async def on_read_resource(
        ctx: ServerRequestContext, params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        uri = str(params.uri)
        try:
            text = await read_resource(uri, current_mcp_identity(ctx))
        except (ValueError, NotAuthenticatedError) as exc:
            # The resource code raises these with messages written for the
            # caller ("Podcast not found: …"). 2.x reports any other
            # exception as an opaque internal error, which is right for
            # the unexpected but would hide these from the model.
            raise MCPError(code=types.INVALID_PARAMS, message=str(exc)) from exc
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, mime_type="text/plain", text=text)]
        )

    server.add_request_handler("resources/list", types.PaginatedRequestParams, on_list_resources)
    server.add_request_handler("resources/read", types.ReadResourceRequestParams, on_read_resource)
