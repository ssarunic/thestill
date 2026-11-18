# thestill.mcp.middleware

## Overview

Request-scoped structured logging for the MCP server. There are two
adapters: a decorator named for stdio handlers, and a Starlette middleware
for an HTTP/SSE transport. Both bind correlation ids into the structlog
contextvars, time the call, and log start, completion, and failure events.

`__init__.py:__all__` exports `MCPLoggingMiddleware` and `log_mcp_stdio`.

## Interface

- `stdio_adapter.py:log_mcp_stdio(f)`: a decorator that returns an async
  wrapper for coroutine functions and a sync wrapper otherwise. It keeps
  `f`'s metadata. Its one use in the package tree is on
  `list_tools` in `thestill/mcp/tools.py:setup_tools` (`@log_mcp_stdio`).
  `setup_tools` also serves the remote HTTP connector
  (`thestill/web/mcp_http.py:125`), so the decorator runs on remote calls
  too, not only on stdio.
- `stdio_adapter.py:_categorize_stdio_error(exception)`: maps an exception to
  `protocol_error`, `resource_not_found`, `validation_error`,
  `tool_execution_error`, or `unknown_error`.
- `logging_middleware.py:MCPLoggingMiddleware`: a `BaseHTTPMiddleware`
  subclass whose `dispatch(request, call_next)` is async. Nothing under
  `thestill/` registers it; the only `add_middleware` call is the docstring
  example. The web app uses its own `LoggingMiddleware` from
  `thestill.web.middleware`.
- `logging_middleware.py:MCPLoggingMiddleware._extract_mcp_method`: checks
  the path first, mapping `/tools/` to `tools/call`, `/resources/` to
  `resources/read`, and `/prompts/` to `prompts/get`. For a POST it then
  falls back to the JSON-RPC `method` field, and otherwise returns
  `unknown`.
- `logging_middleware.py:MCPLoggingMiddleware._categorize_error(status_code)`
  maps status codes to error categories:

  | Status code | Category |
  | --- | --- |
  | Below 400 | `None` |
  | 400 | `protocol_error` |
  | 404 | `resource_not_found` |
  | 500 and above | `tool_execution_error` |
  | Any other 4xx | `unknown_error` |

## Invariants

- The request id is the first 8 characters of a `uuid4`. It is minted once
  per HTTP request or once per stdio call
  [`logging_middleware.py:MCPLoggingMiddleware.dispatch`,
  `stdio_adapter.py:log_mcp_stdio`].
- Exceptions are logged and then re-raised, never swallowed, on both
  transports [`logging_middleware.py:MCPLoggingMiddleware.dispatch`,
  `stdio_adapter.py:log_mcp_stdio`].
- Both adapters call `structlog.contextvars.clear_contextvars()` in
  `finally`. This clears every bound contextvar, including any that an outer
  layer bound [`logging_middleware.py:MCPLoggingMiddleware.dispatch`,
  `stdio_adapter.py:log_mcp_stdio`].
- On stdio, `mcp_method` is the wrapped function's `__name__`, not a
  JSON-RPC method string [`stdio_adapter.py:log_mcp_stdio`].
- The decorator does not check the transport. `registration.py:register_tools`
  calls `list_tools` on every `tools/list` and again to validate arguments
  on every `tools/call` (`thestill/mcp/registration.py:89,98`), on stdio and
  on the remote connector alike. Each of those calls binds
  `transport="stdio"`, `mcp_method="list_tools"`, and a fresh `request_id`
  (`stdio_adapter.py:61-64`).
- Known issue: on a remote `tools/call`, the decorator's
  `clear_contextvars()` (`stdio_adapter.py:104`) runs during argument
  validation, before the tool itself runs. It drops any request-scoped
  contextvars bound earlier in that context, and remote log lines are
  tagged `transport="stdio"` while the decorator is active.
- The decorator decides between the sync and async wrapper once, at
  decoration time, with `asyncio.iscoroutinefunction`
  [`stdio_adapter.py:log_mcp_stdio`].
- stdio errors are categorized in a fixed order, from the lowercased
  message text and the exact class name rather than `isinstance`
  [`stdio_adapter.py:_categorize_stdio_error`]:
  1. `protocol_error` when the message contains `invalid`, `malformed`, or
     `protocol`.
  2. `resource_not_found` when it contains `not found`, or for
     `FileNotFoundError` and `KeyError`.
  3. `validation_error` when it contains `validation`, or for
     `ValidationError`.
  4. `tool_execution_error` for `RuntimeError`, `OSError`, and `IOError`.
  5. `unknown_error` otherwise.
- Extracting the method never raises: a JSON decode error falls back to
  `unknown`. The code reassigns `request._body` so downstream handlers can
  read the body again [`logging_middleware.py:MCPLoggingMiddleware._extract_mcp_method`].
- An HTTP completion is logged at `info` below status 400 and at `error`
  from 400 up [`logging_middleware.py:MCPLoggingMiddleware.dispatch`].

## Side Effects

- Binds the structlog contextvars `request_id`, `mcp_method`, and
  `transport` (`http` or `stdio`)
  [`logging_middleware.py:MCPLoggingMiddleware.dispatch`,
  `stdio_adapter.py:log_mcp_stdio`].
- HTTP log events are `mcp_request_started`, `mcp_request_completed` (with
  `status_code`, `duration_ms`, and `error_category`), and
  `mcp_request_failed` [`logging_middleware.py:MCPLoggingMiddleware.dispatch`].
- stdio log events are `mcp_stdio_request` and `mcp_stdio_completed`, both at
  debug level, and `mcp_stdio_failed`, which logs `error=str(e)` and
  `exc_info` [`stdio_adapter.py:log_mcp_stdio`].
- Sets the `X-Request-ID` response header
  [`logging_middleware.py:MCPLoggingMiddleware.dispatch`].
- Reads the whole POST body into memory to parse the JSON-RPC method
  [`logging_middleware.py:MCPLoggingMiddleware._extract_mcp_method`].
- stdout belongs to the MCP protocol on stdio, so logs must go to stderr.
  The module docstring states this rule, and the global structlog setup
  enforces it; this module does not configure output itself
  [`stdio_adapter.py`].
- No database, filesystem, network, or environment-variable access.

## Verification

- Run: `./venv/bin/pytest tests/unit/mcp`
- Run: `make test` for the full suite.
