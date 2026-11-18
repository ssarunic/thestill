# thestill.web

## Overview

The FastAPI web server behind `thestill server`. `app.py:create_app` is the
application factory. It:

- builds the repositories and services;
- holds them in one `dependencies.py:AppState`;
- starts the background task worker and the schedulers in its lifespan;
- installs middleware;
- mounts the REST routers (`./routes/`), the remote MCP endpoint at `/mcp`
  (`mcp_http.py`) with its OAuth server (`mcp_oauth.py`), and the built React
  SPA from `static/`.

For signed-out visitors in multi-user mode with public pages on, the SPA
catch-all serves crawlable public pages:

- a per-page `<head>`, `robots.txt` and the sitemap (`public_shell.py`);
- server-written body HTML (`public_prerender.py`);
- one shared visibility and cache policy (`public_access.py`).

## Interface

### Entry points

- `__init__.py:__all__` exports `create_app`, `BackgroundWebhookServer`,
  `ExistingServerInfo`, `is_thestill_server_running` and `webhook_server_context`.
- `app.py:create_app(config=None) -> FastAPI` is the factory. It can be launched with
  `uvicorn thestill.web.app:create_app --factory`.
- `app.py:CachedStaticFiles` is the `/assets` mount and adds long-lived cache headers.
- `app.py:SERVICE_WORKER_FILE = "sw.js"` names the reader's service worker (spec #116),
  served from the site root so its scope is the whole site.
- `app.py:log_unhandled_exception` logs unhandled errors.

### Dependencies (`dependencies.py`)

- `AppState`: a dataclass of every shared service and repository, for example
  `config`, `repository`, `queue_manager`, `task_worker`, `auth_service`,
  `podcast_service`, `inbox_service`, `briefing_service`, `search_backend`, `abuse`,
  `mcp_token_service`, `mcp_oauth_service` and `transcript_edit_service`. Optional
  services default to `None`.
- `get_app_state` returns `request.app.state.app_state`.
- Auth: `get_current_user` (returns `None` when there is no user), `require_auth` (401)
  and `require_admin` (403).
- `is_effective_admin` decides whether a user counts as an admin.
- `user_rate_limit`, `user_search_rate_limit` and `enforce_read_only` are the other
  router guards.
- `AUTH_COOKIE_NAME = "auth_token"`.

### Permissions (`permissions.py`)

- `TRANSCRIPT_EDIT = "transcript.edit"`, `DRIFT_ALIGN = "drift.align"` (spec #116: the
  reader may align the transcript with the listener's copy of the audio) and
  `PERMISSIONS = (TRANSCRIPT_EDIT, DRIFT_ALIGN)`, the order `/api/auth/status` lists them.
- `has_permission`, `permissions_for`, and `require_permission(permission)`, a
  dependency factory that returns 401 or 403.

### Responses and errors

- `responses.py`:
  - `api_response` builds the envelope `{status, timestamp, ...}`;
  - `paginated_response` adds `count`, `total`, `offset`, `limit`, `has_more` and
    `next_offset`;
  - `etag_json_response` adds a weak SHA-1 `ETag` and answers 304 on a match;
  - `not_found`, `bad_request`, `conflict` and `parse_iso_datetime` raise 404, 400, 409
    and 400.
- `error_handlers.py:register_abuse_handlers(app)` turns `AbusePreventionError` into
  `{"detail": ...}` with the error's own status code and headers.

### Remote MCP (`mcp_http.py`, `mcp_oauth.py`)

- `mcp_http.py`:
  - `McpHttpRuntime` guards `/mcp/{token}`, a per-user capability URL.
  - `McpBearerGuard` guards the exact path `/mcp` with OAuth bearer tokens.
  - `build_mcp_http` returns `None` unless `config.mcp_http_enabled` is set.
  - `mount_base_url` builds the connector URL.
  - `MOUNT_PATH = "/mcp"`.
- `mcp_oauth.py`:
  - `build_mcp_oauth(config, repos, runtime) -> (McpOAuthService, routes)` builds the
    OAuth service and its routes.
  - The SDK provides `/authorize`, `/token`, `/register` and `/revoke`.
    `/.well-known/oauth-authorization-server` is replaced with thestill's own document.
  - `/.well-known/oauth-protected-resource` is served both at the root and suffixed
    with `/mcp`.
  - `ThestillOAuthProvider` and `ThestillClient` adapt the SDK.
  - `OAUTH_PATHS = ("authorize", "token", "register", "revoke")`.
  - `CONSENT_PATH = "/oauth/consent"`.

### Public pages

- `public_access.py`:
  - `PublicVisibility` (with `get_public_visibility`) answers whether a podcast,
    episode or entity is public.
  - `TtlCache`, `cache_only` and `CacheMiss` cache public payloads.
  - `apply_public_cache_headers`, `public_rate_limit` and `has_valid_session` handle
    caching, rate limits and session checks.
  - `EntityIndexFacts` and `entity_is_indexable` decide which entity pages are indexed.
- `public_shell.py`:
  - `ShellRenderer` and `get_shell_renderer` build `HeadTags` for a path.
  - `render_head` and `inject` write the tags into `index.html`.
  - `SitemapBuilder` and `get_sitemap_builder` produce the sitemap.
  - `ROBOTS_PUBLIC` and `ROBOTS_PRIVATE` are the two `robots.txt` bodies.
    `TRAINING_CRAWLERS` and `ENTITY_HEAVY_CRAWLERS` name the user agents that
    `ROBOTS_PUBLIC` gives their own groups.
  - `ShellTemplate` reads `index.html`.
  - `shell_kind`, `has_legacy_deep_link` and `move_deep_links_to_fragment` classify
    paths and move old query deep links into the fragment.
  - `log_shell_served` logs each served page.
  - `LookupThrottled` and `LookupShed` signal the 429 and 503 outcomes.
- `public_prerender.py`:
  - `Prerenderer` and `get_prerenderer` build the page body. `Prerenderer` has
    `render`, `slot`, `enabled` and `log_shed`.
  - `Outcome`, `build_fallback`, `render_body` and `inject_body` produce and insert
    the HTML.
  - `PRERENDER_KINDS = {episode, podcast, entity, landing, top}`.
  - `BOOTSTRAP_ID = "thestill-bootstrap"` and `BOOTSTRAP_VERSION = 1` identify the
    bootstrap data block.

### Analytics and in-process tasks

- `analytics_request.py`:
  - `capture_event` sends one usage event to the analytics sink.
  - `may_measure` and `request_gpc` check consent and Global Privacy Control.
  - `visitor_key`, `daily_visitor_key` and `login_flow_key` derive anonymous ids.
- `task_manager.py`:
  - `TaskManager`, with `get_task_manager()` returning the process singleton, tracks
    in-memory tasks.
  - `TaskType`: `refresh`, `download`, `downsample`, `transcribe`, `clean`,
    `summarize` and `add_podcast`.
  - `TaskStatus`: `pending`, `running`, `completed` and `failed`.
  - `Task` describes one run.
- `background_server.py`:
  - `BackgroundWebhookServer` runs uvicorn in a daemon thread so CLI runs can receive
    webhooks.
  - `webhook_server_context` wraps it as a context manager.
  - `ExistingServerInfo` and `is_thestill_server_running` detect a server already
    running.

### HTTP surface outside the routers (`app.py:create_app`)

All of these are registered inside `if static_dir.exists():` (`app.py:968`). Without a
`static/` build none of them exist, and their paths fall through to the default 404.

- `GET|HEAD /robots.txt`
- `GET|HEAD /sitemap.xml` and `/sitemap-{page}.xml`
- `GET|HEAD /{indexnow_key}.txt`, only when `config.indexnow_key` is set
- the `/assets` static mount, only when `static/assets/` exists
- the SPA catch-all `GET|HEAD /{full_path:path}` (`serve_spa`)

### Subdirectories

- [Middleware](./middleware/EGM.md): logging, security-header and body-size middleware,
  plus the in-process sliding-window rate limiter and client-IP resolution.
- [Routes](./routes/EGM.md): the FastAPI routers and the guard each one is
  mounted with. The route list itself is in `docs/web-server.md`.
- [Services](./services/EGM.md): the readiness probes (`HealthService`) and the
  ElevenLabs webhook transcript writer (`WebhookTranscriptProcessor`).
- [Frontend](./frontend/EGM.md): the React + Vite single-page frontend.
- `static/` (not a contract): the built SPA (`index.html`, `assets/`) that `serve_spa`
  serves. `THESTILL_STATIC_DIR` overrides its location.

## Invariants

### Routing and access

- **Default deny** (`app.py:create_app`).
  - Every `/api` router except `auth`, `api_public` and the three operator routers is
    mounted with `require_session = [require_auth, enforce_read_only, user_rate_limit]`
    (`app.py:875`).
  - Operator routers (`/api/status`, `/api/dashboard`, `/api/admin`) use
    `require_operator = [require_admin]` only (`app.py:879`). `require_admin` still
    authenticates the caller.
  - `/api/public` is mounted only when `config.multi_user and config.public_pages_enabled`.
  - Known issue: the operator routers skip `enforce_read_only` and `user_rate_limit`
    (`app.py:879`, `:881`, `:898`, `:901`). Read-only mode exempts admins anyway, but
    calls under `/api/status`, `/api/dashboard` and `/api/admin` are never counted
    against the per-user `USER_API_LIMIT` budget.
- **Admin rule.** `dependencies.py:is_effective_admin` is the single definition of
  "admin".
  - In single-user mode the local user is always admin.
  - In multi-user mode the user's `is_admin` flag is read fresh on every request, which
    `mcp_http.py:McpHttpRuntime.dispatch` relies on as well.
  - `require_admin` returns 401 when there is no user and 403 when the user is not an
    admin.
- **Token lookup.** `dependencies.py:_get_token_from_request` reads the `auth_token`
  cookie first, then `Authorization: Bearer`. In single-user mode
  `auth_service.get_current_user` always returns the default user.
- **Read-only mode.** `dependencies.py:enforce_read_only` returns 503 (`ReadOnlyError`)
  for non-admin POST, PATCH, PUT and DELETE when the `read_only` setting is on. GET,
  HEAD and OPTIONS always pass.
- **Permissions.** `permissions.py:has_permission` never grants an unknown permission,
  and every permission is admin-only today. `DRIFT_ALIGN` is also a kill switch: nobody
  holds it unless `config.drift_alignment_enabled` is `True` (`DRIFT_ALIGNMENT_ENABLED`,
  on by default). `require_permission` raises `ValueError` at import for an unknown
  name. New routes should use it instead of `require_admin`.
- **Route order.** The `/mcp` mount and the OAuth routes are registered before the SPA
  catch-all. `serve_spa` returns `None` for these paths:
  - `api/`, `webhook/`, `mcp/`, `docs`, `redoc`, `openapi.json` and `health`;
  - `OAUTH_PATHS`;
  - `.well-known/oauth-*`.
  It serves only files that resolve inside `static/`, with
  `Cache-Control: public, max-age=86400`, except `sw.js` (`SERVICE_WORKER_FILE`), which
  is sent with `no-cache` so browsers re-check it on every load. A missing `sw.js` gets
  a plain-text 404, never the HTML shell.
- **Remote MCP** (`mcp_http.py:McpHttpRuntime.__call__`).
  - Any unknown, revoked or expired token, any extra path segment and any IP that has
    used up its miss budget get an empty 404, so nothing reveals that the endpoint
    exists.
  - A token over its rate limit gets 429 with `Retry-After: 60`.
  - Suspended and banned accounts are refused on both doors (`McpHttpRuntime.admit`).
- **OAuth bearer** (`mcp_http.py:McpBearerGuard`).
  - A request with no token gets 401 with a
    `WWW-Authenticate: Bearer resource_metadata=...` challenge.
  - `offline_access` is removed from the request's scopes.
- **OAuth server** (`mcp_oauth.py`).
  - Registrations default to public clients (`token_endpoint_auth_method="none"`).
  - Registration bodies are capped at 16 KiB and rate-limited per IP
    (`allow_oauth_registration`).
  - `_loopback_port_match` lets loopback `http` redirects match on any port. It never
    applies to `https` or other hosts.
  - The metadata lists only `S256` for PKCE.
  - The consent request id travels in the URL fragment.

### Public pages

- **Visibility** (`public_access.py:PublicVisibility`).
  - A podcast is public when `repository.is_podcast_public` says so. Per the module
    docstring, that means it is not a synthetic import container.
  - An episode is public when it has a slug, a real summary preview and a public podcast.
  - An entity is public unless it is hidden.
  - The hidden sets are cached for 60 s. Every public cache key includes
    `PublicVisibility.fingerprint()`, so a takedown reaches every cached payload on the
    next refresh.
- **Caching and rate limits.**
  - `public_access.py:apply_public_cache_headers` sends
    `public, max-age=300, stale-while-revalidate=300` to anonymous callers and
    `private, no-cache` to any request with a credential, always with
    `Vary: Cookie, Authorization`.
  - `public_rate_limit` skips only valid sessions, so a junk cookie still pays.
- **Head tags.**
  - `public_shell.py:render_head` HTML-escapes every value.
  - `safe_json` escapes `<`, `>`, `&` and U+2028/U+2029 inside script blocks.
  - JSON-LD is emitted only for indexed pages.
  - Invalid slugs or entity types return 404 before any query (`ShellRenderer.head_for`).
  - A cache miss costs an anonymous caller one public-bucket token, or raises
    `LookupThrottled`, which becomes a 429.
  - Redirects are never cached.
- **SPA catch-all** (`app.py:create_app`, `serve_spa`).
  - A re-typed entity URL gets a 301 to its current path (`HeadTags.redirect_path`).
  - A legacy `?t=` or `?about=` query on an episode or entity page gets a 301 to the
    clean URL, with the value moved to the fragment.
  - A lookup failure on a lookup path returns 503 with `Retry-After: 120`.
  - The HTML is always sent with `Cache-Control: no-cache, must-revalidate`.
- **Prerendering** (`public_prerender.py:Prerenderer`).
  - Only anonymous 200 requests for `PRERENDER_KINDS` get body HTML. A request with any
    query string gets only the page header (`header_only`).
  - The output never depends on the User-Agent.
  - Uncached builds are capped by a `BoundedSemaphore(prerender_max_concurrent)`. When
    no slot frees up within `prerender_slot_wait_seconds`, the page is shed: the shell
    is served without a body.
  - The `public_prerender_enabled` setting falls back to off when it cannot be read.
  - `_safe_href` lets only `http`, `https` and site-relative links through.
  - The episode rail lists the people who take part (a `role` of guest, host or
    recurring, from `api_public.py:_episode_quote_set`) under "In this episode", each
    with its role and never folded (`_taking_part`). Everyone else goes in the
    "mentioned" groups, with people under "People mentioned" (`episode_fallback`).
- **Static public paths** (`public_shell.py:_PUBLIC_STATIC_PATHS`). `/`, `/top` and the
  connector guide `/claude` get indexed head tags; `/terms` and `/privacy` are
  `noindex`. `/claude` is not a `PRERENDER_KINDS` kind, so it gets no body HTML.
- **Robots and sitemap.** Without public pages, `robots.txt` is `ROBOTS_PRIVATE`
  (disallow everything) and the sitemap returns 404. `ROBOTS_PUBLIC` blocks the
  `TRAINING_CRAWLERS` from everything. `ENTITY_HEAVY_CRAWLERS` (Reflectionbot, PetalBot)
  get their own group that disallows `/entities/` and `/api/public/entities/` and
  repeats every other `*` rule (`_ENTITY_HEAVY_RULES`), because a crawler that matches a
  named group ignores `*`. The sitemap always lists `/`, `/top` and `/claude`
  (`SitemapBuilder._collect`). It is cached for 1 h, keyed on the visibility
  fingerprint, and split into parts of 40,000 URLs.

### Startup, middleware and responses

- **Startup** (`app.py:create_app`).
  - On Postgres, `run_alembic_upgrade` runs before `make_repositories` when
    migrate-on-startup is enabled. A failed migration stops the app from booting.
  - Any `*` CORS origin raises `ValueError`.
  - `/docs`, `/redoc` and `/openapi.json` exist only in development or with
    `config.enable_docs`.
  - Unhandled exceptions return `{"detail": "Internal Server Error"}`; the exception
    type and message are shown only in development.
- **Lifespan order** (`app.py:create_app` lifespan).
  - `validate_transcription_provider` runs first and fails fast.
  - Then `queue_manager.recover_interrupted_tasks`, which skips `TRANSCRIBE` for
    `google` and `elevenlabs`.
  - In single-user mode the default user is then auto-followed to every podcast.
  - Then, in order: the task worker, the `RefreshScheduler` (only if enabled), the
    `BriefingScheduler` (always), the `PeriodicRunner`, the warmup threads and the MCP
    lifespan.
  - Shutdown runs in the reverse order and ends with `analytics_sink.shutdown()`.
- **Middleware** (`app.py:create_app`). Middleware is added in this order: GZip (1 KiB
  minimum), `LoggingMiddleware`, `SecurityHeadersMiddleware`, `BodySizeLimitMiddleware`,
  then CORS when origins are configured. The last one added is the outermost.
  `BodySizeLimitMiddleware` gets `route_limits=[("/webhook/", ...)]` and
  `suffix_limits=[("/drift-outcome", api_podcasts.DRIFT_OUTCOME_MAX_BYTES)]` (4096 bytes),
  so a drift report is capped before FastAPI reads its body (`app.py:813`).
- **ETags.** `responses.py:etag_json_response` computes the ETag from the data without
  the envelope timestamp and sends `Cache-Control: private, no-cache`.
- **Privacy** (`analytics_request.py`).
  - `capture_event` never raises.
  - It sends nothing when the sink is off or the user has opted out (`consent_of`).
  - Under GPC (`Sec-GPC: 1`) it sends no visitor key.
  - The visitor key is an HMAC of IP and user agent, keyed with the server secret and
    the UTC date. The raw IP is never stored.
- **Task manager** (`task_manager.py:TaskManager`).
  - `start_task` returns `None` while a task of the same `TaskType` is running.
  - All changes happen under a `threading.Lock`.
  - The history keeps at most `HISTORY_SIZE = 200` runs.
- **Detecting an existing server.** `background_server.py:is_thestill_server_running`
  probes `GET /` and expects JSON with `service == "Thestill"` and `status == "ok"`.
  No route in `thestill/web` returns that payload: with a static build present, `/` is
  served by `serve_spa`.

## Side Effects

- **At app creation** (`app.py:create_app`):
  - may run Alembic migrations;
  - opens the SQLite or Postgres repositories (`make_repositories`);
  - builds the abuse services and the email sender;
  - builds the embedding model and the search backend; the weights load lazily.
- **During the lifespan:**
  - `queue_manager.recover_interrupted_tasks` writes to the queue.
  - Single-user auto-follow writes follow rows.
  - Background threads and loops: the task worker, the refresh and briefing schedulers,
    and `PeriodicRunner` jobs (usage housekeeping, `admin_alerts`, `admin_digest`, MCP
    OAuth housekeeping, `transcript_edit_refresh`).
  - Daemon threads `embedding-model-warmup` and `reranker-warmup` load models.
- **MCP** (`mcp_http.py`):
  - `McpHttpRuntime` calls `_tokens.touch_last_used` and records connector days
    (`_record_connector_day`, at most once per reader per UTC quarter hour) and sends
    `McpToolCall` analytics.
  - `McpBearerGuard` calls `McpOAuthService.touch`.
  - Both record per-IP token misses.
- **OAuth** (`mcp_oauth.py:ThestillOAuthProvider`). On worker threads, the provider
  calls `McpOAuthService` to register, park, exchange and revoke. Client lookups may
  fetch a client metadata document over the network (`CimdResolver`), with at most 8
  lookups at a time.
- **In-memory state:**
  - `task_manager.py:_task_manager`, the singleton;
  - `public_access.py:_ALL_CACHES`, plus the visibility caches;
  - the prerender semaphore;
  - `ShellTemplate`, which re-reads `index.html` whenever its mtime changes.
- **Logs:**
  - `public_shell.py:log_shell_served` logs `public_shell.served`, holding back
    `client_ip` and the full user agent.
  - `public_prerender.py` logs `public_prerender.shed` and `public_prerender.failed`.
  - `app.py:log_unhandled_exception` redacts capability paths.
  - `app.py:create_app` installs `install_uvicorn_access_redaction` when MCP is mounted.
- **`background_server.py:BackgroundWebhookServer.start`** checks that the port is free
  by binding a socket (`_is_port_available`), starts a uvicorn daemon thread built with
  `create_app(config)`, waits for it to signal start, then sleeps 0.5 s. It makes no
  HTTP request. The `requests.get` probe of `GET /` lives in
  `is_thestill_server_running`.

## Verification

- Run: `./venv/bin/pytest tests/unit/web tests/integration/web` (full suite: `make test`)
