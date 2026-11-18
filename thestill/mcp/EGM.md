# thestill.mcp

## Overview

The Model Context Protocol (MCP) server. It exposes Thestill's podcast
library, pipeline, entity layer, corpus search, inbox, and admin analytics
as MCP tools, and podcasts and episodes as `thestill://` resources. One
handler surface is served over two transports:

- **stdio**: `server.py:main`, the `thestill-mcp` entry point, builds
  `server.py:ThestillMCPServer`, which runs `mcp.server.stdio.stdio_server`.
- **Remote HTTP connector**: `thestill/web/mcp_http.py` calls
  `resources.py:setup_resources` and `tools.py:setup_tools` with the web
  server's `Config` and an `on_tool_called` counter.

Handlers are registered against the mcp 2.x low-level `Server` through
`registration.py`. Caller identity comes from `identity.py`, and the
per-tool scope table lives in `scopes.py`.

Subdirectory: [`middleware`](./middleware/EGM.md) holds the structured-logging
adapters. `log_mcp_stdio` decorates `list_tools`.

## Interface

### Tools (`tools/list`, `tools/call`)

What each tool does, its arguments and its defaults are documented in
[docs/mcp-usage.md](../../docs/mcp-usage.md#available-tools), which
`tests/docs/test_mcp_tool_drift.py` keeps in step with the registered names.
This contract records only where tools are defined and the access rules.

- Definitions: `tools.py:setup_tools.list_tools` (library and pipeline
  tools), `entity_tools.py:entity_tool_definitions`,
  `search_tools.py:search_tool_definitions`,
  `inbox_tools.py:inbox_tool_definitions` and
  `analytics_tools.py:analytics_tool_definitions`. The server declares 24
  tools.
- Titles and behaviour hints come from `annotations.py:_HINTS_BY_TOOL`
  (spec #109), applied by `annotations.py:annotate`.
- Required scope per tool comes from `scopes.py:_SCOPE_BY_TOOL`:
  - `read`: `list_podcasts`, `list_episodes`, `get_status`, `get_transcript`,
    `get_summary`, `find_mentions`, `list_quotes_by`, `get_episode_clip`,
    `get_entity`, `list_episodes_by_entity`, `search_corpus`, `list_inbox`,
    `get_analytics` (admins only).
  - `follows`: `add_podcast`, `remove_podcast`, `import_episode`,
    `send_to_inbox`.
  - `pipeline`: `refresh_feeds`, `download_episodes`, `downsample_audio`,
    `transcribe_episodes`, `clean_transcripts`, `process_episode`,
    `summarize_episodes`.
- Remote and stdio differ for some tools: a remote `remove_podcast` only
  unfollows, and remote `list_podcasts` / `get_status` see followed shows
  only (see Invariants).

### Resources (`resources/list`, `resources/read`)

`resources.py:setup_resources` serves these URI templates:

- `thestill://podcasts/{podcast_id}`
- `thestill://podcasts/{podcast_id}/episodes/{episode_id}`
- `thestill://podcasts/{podcast_id}/episodes/{episode_id}/transcript`
- `thestill://podcasts/{podcast_id}/episodes/{episode_id}/audio`
- `thestill://podcasts/{podcast_id}/episodes/{episode_id}/summary`

### Python symbols

- `server.py:ThestillMCPServer` (provides `run()`) and `server.py:main`.
- `tools.py:setup_tools(server, storage_path, config=None, on_tool_called=None)`
  and `tools.py:_MUTATING_TOOLS`.
- `resources.py:setup_resources(server, storage_path, config=None)`.
- `registration.py:register_tools`, `register_resources`, and
  `tool_result_ok`. The callable types are `ListToolsFn`, `CallToolFn`,
  `ListResourcesFn`, `ReadResourceFn`, and `ToolCalledFn`.
- `identity.py:McpIdentity`, a frozen dataclass with the fields `user`,
  `is_admin`, `scopes`, `remote`, and `gpc`, and the properties `is_remote`,
  `is_anonymous_remote`, and `effective_scopes`.
- `identity.py:STDIO` and `identity.py:ANONYMOUS_REMOTE`.
- `identity.py:ScopeError`, `NotAuthenticatedError`, `current_mcp_identity`,
  `require_authenticated`, `require_scope`, `visible_tools`, and
  `remote_call_limiter`.
- `identity.py:REMOTE_CALL_WORKERS` (4) and the state keys `STATE_USER`,
  `STATE_IS_ADMIN`, `STATE_SCOPES`, and `STATE_GPC`.
- `scopes.py:scope_for_tool`, `registered_tools`, and `effective_scopes`.
- `annotations.py:ToolHints`, a frozen dataclass with the fields `title`,
  `read_only`, and `destructive` (default `False`), and the functions
  `hints_for_tool`, `annotated_tool_names`, and `annotate`.
- `errors.py:McpUserError`, a `ValueError` subclass, and
  `errors.py:public_error_message`.
- `utils.py:resolve_identifier`, `parse_thestill_uri`, and
  `NumericIdentifierRefused`, an `McpUserError` subclass.
- `entity_tools.py:ENTITY_TOOL_NAMES` and `dispatch_entity_tool`.
- `search_tools.py:SEARCH_TOOL_NAMES` and `dispatch_search_tool`.
- `inbox_tools.py:INBOX_TOOL_NAMES`, `INBOX_WRITE_TOOL_NAMES`,
  `LIST_DEFAULT_LIMIT`, `LIST_MAX_LIMIT`, and `dispatch_inbox_tool`.
- `analytics_tools.py:ANALYTICS_TOOL_NAMES`, `DEFAULT_TOOL_RANGE`,
  `DETAIL_LEVELS`, `CACHE_TTL_SECONDS` (600), `SUMMARY_ROWS` (10),
  `SUMMARY_COHORT_ROWS` (6), `new_analytics_cache`, and
  `dispatch_analytics_tool`.

## Invariants

- Scopes fail closed. A tool with no entry in `_SCOPE_BY_TOOL` is hidden from
  remote `tools/list` and refused on remote `tools/call`. stdio never checks
  scopes [`scopes.py:scope_for_tool`, `identity.py:require_scope`].
  `tests/unit/mcp/test_scopes.py` asserts that every tool has an entry.
- `pipeline` counts only while the caller is an admin at call time, so a
  demoted admin loses it on the next call
  [`scopes.py:effective_scopes`, `identity.py:McpIdentity.effective_scopes`].
- Listing tools is advisory. The control is `require_scope`, the first check
  in `tools.py:setup_tools._call_tool_sync`
  [`identity.py:visible_tools`].
- Every listed tool carries a `title` and explicit `readOnlyHint` and
  `destructiveHint` annotations. `list_tools` returns
  `annotate(visible_tools(identity, tools))`, so filtering by scope happens
  before annotation [`tools.py:setup_tools.list_tools`,
  `annotations.py:annotate`]:
  - `readOnlyHint` is true exactly for the `read`-scope tools.
  - `destructiveHint` is true only for `remove_podcast`.
  - `annotate` returns copies and leaves a tool without a registry entry
    unchanged.
  - `tests/unit/mcp/test_annotations.py` asserts that the registry names
    exactly the declared tools, that titles are distinct, and that the
    hints agree with the scopes.
- The public connector guide `thestill/web/frontend/public/help/claude.md`
  lists exactly the `read` and `follows` tools except `get_analytics`, with
  read-only tools under "Read tools." and the rest under "Write tools."
  [`tests/unit/mcp/test_claude_guide.py`].
- Identity rules [`identity.py:current_mcp_identity`]:
  - Only a request with no HTTP request object is `STDIO`, which has admin
    semantics, no scopes, and corpus-wide access.
  - An HTTP request whose ASGI state has no user becomes
    `ANONYMOUS_REMOTE`, which fails closed everywhere.
- Mutation guard [`tools.py:setup_tools._call_tool_sync`]:
  - `_MUTATING_TOOLS` holds the pipeline tools, `add_podcast`,
    `remove_podcast`, `import_episode`, and `send_to_inbox`.
  - These tools are refused for non-admins while the `read_only` setting is
    on (spec #104).
  - They are also rate-limited by `enforce_mcp_mutation_quota`, keyed by the
    user id on remote calls and by `MCP_SESSION_KEY`, or a per-process key,
    on stdio. A refusal returns `retry_after_seconds: 60`.
- The legacy numeric podcast index is accepted only on stdio. Over the remote
  connector it raises `NumericIdentifierRefused` [`utils.py:resolve_identifier`,
  `utils.py:parse_thestill_uri`].
- Exception text is never sent to the client. Only `McpUserError`,
  `ScopeError`, `NotAuthenticatedError`, `RateLimitExceeded`,
  `AbusePreventionError`, and per-call `also_authored` types pass their
  message through. Anything else becomes `Internal error (ref <8-hex>)`
  [`errors.py:public_error_message`]. The function never reads config: it
  relays `<Type>: <message> (ref <8-hex>)` only when the caller passes
  `expose_detail=True`. The tool dispatcher and resource reads pass
  `config.environment == "development"` [`tools.py:setup_tools`,
  `resources.py:setup_resources`]. The backstop in
  `registration.py:register_tools._call_tool` passes no `expose_detail`, so
  an exception that escapes the dispatcher stays opaque even in development.
- `call_tool` validates arguments against the schema the caller was shown
  (`jsonschema`). A tool raising returns an `is_error` result rather than a
  protocol error, and `on_tool_called` can never change a result
  [`registration.py:register_tools`].
- A failed tool call is either `is_error`, or a JSON payload with
  `"success": false` or an `error` key [`registration.py:tool_result_ok`].
- Remote tool calls and resource reads run on worker threads behind a
  per-`Server` `CapacityLimiter(4)`. They never use AnyIO's default limiter
  [`identity.py:remote_call_limiter`, `tools.py:setup_tools.call_tool`,
  `resources.py:setup_resources.read_resource`].
- `get_analytics` is listed and runs only for `identity.is_admin`. Other
  callers get `Unknown tool` [`analytics_tools.py:analytics_tool_definitions`,
  `dispatch_analytics_tool`]. The tool reports `available: false` unless
  `AnalyticsService.available`, which needs multi-user mode on Postgres.
- Inbox tools act for the remote user. On stdio they act for the default
  user only in single-user mode; multi-user stdio refuses
  [`tools.py:setup_tools._call_tool_sync`].
- `list_inbox` never marks rows read [`inbox_tools.py` module docstring].
- `send_to_inbox` delivers with `source="ad_hoc"`
  [`inbox_tools.py:_send_to_inbox`].
- The entity tools resolve `podcast_id` through the shared resolver before
  dispatch, so an unknown podcast returns `Podcast not found`
  [`tools.py:setup_tools._call_tool_sync`].
- When only one of `date_from` and `date_to` is given, the missing bound
  defaults to the epoch or now [`entity_tools.py:_parse_date_range`].

## Side Effects

- Constructing the server builds repositories through
  `repositories.factory.make_repositories` (SQLite, or Postgres when
  `DATABASE_URL` is set), the abuse services, the search backend, and the
  service objects. `AuthService` is built lazily
  [`tools.py:setup_tools`, `resources.py:setup_resources`].
- Reads the environment variable `MCP_SESSION_KEY` [`tools.py:setup_tools`].
- Pipeline tools do the following [`tools.py:setup_tools._call_tool_sync`]:
  - Download audio over the network (`AudioDownloader`).
  - Call transcription providers (`_get_transcriber`) and LLM providers
    (`_get_cleaning_processor`, `_get_summarizer`).
  - Write raw transcripts, cleaned transcripts, summaries, and summary
    manifests through `config.file_storage.write_text` or into
    `PathManager` directories.
  - Update episode rows through the `feed_manager.mark_episode_*` methods.
- `refresh_feeds` fetches RSS feeds and enqueues newly discovered episodes on
  `repos.queue_manager` [`tools.py:setup_tools`].
- `add_podcast`, `remove_podcast`, `import_episode`, and `send_to_inbox`
  write podcasts, follows, and inbox rows through the services, and may start
  pipeline tasks [`tools.py`, `inbox_tools.py`].
- The `get_analytics` report is cached per range for 600 s
  [`analytics_tools.py:new_analytics_cache`].
- The `episode` resource checks whether the transcript and summary files
  exist on local disk [`resources.py:_read_resource_sync`].
- Log events include `mcp_session_key_initialized`, `mcp_internal_error`
  (with `error_ref`), `mcp_request_without_identity`,
  `initializing_mcp_server`, `mcp_server_initialized`, and
  `fatal_error_in_mcp_server` [`tools.py`, `errors.py`, `identity.py`,
  `server.py`]. `server.py` calls `configure_structlog()` so logs go to
  stderr and stdout stays reserved for JSON-RPC.
- `on_tool_called(identity, name, ok)` reports usage events (spec #95) to the
  HTTP runtime [`registration.py:register_tools`].

## Verification

- Run: `./venv/bin/pytest tests/unit/mcp`
- Run: `make test` for the full suite.
