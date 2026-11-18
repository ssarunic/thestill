# thestill.web.middleware

## Overview

ASGI middleware and rate-limit primitives for the FastAPI web server. Three
Starlette `BaseHTTPMiddleware` classes (request logging, security headers,
request-body caps) are added in `thestill/web/app.py:create_app`. The
in-process sliding-window limiter in `rate_limit.py` backs every per-IP,
per-user and per-MCP-token budget used by routes, the MCP guards and the
public pages. None of these files write to the database or the filesystem,
and none make network calls.

## Interface

- `__init__.py:__all__` re-exports `BodySizeLimitMiddleware`, `LoggingMiddleware`,
  `SecurityHeadersMiddleware`, `RateLimit`, `RateLimitExceeded`, `AUTH_LIMIT`,
  `WEBHOOK_LIMIT`, `MCP_MUTATION_LIMIT`, `rate_limit_dependency`,
  `enforce_mcp_mutation_quota`, `resolve_client_ip` and `trusted_proxy_set`.
  `reset_for_testing` is re-exported as `reset_rate_limits_for_testing`.
- `body_size.py:BodySizeLimitMiddleware(app, *, default_limit, route_limits=(), suffix_limits=())`
  rejects request bodies that are too large, with a separate limit per path prefix
  (`route_limits`) or per path suffix (`suffix_limits`, for routes whose variable part
  comes first, such as `/drift-outcome`).
- `logging_middleware.py:LoggingMiddleware` logs each request and binds a correlation ID to it.
- `security_headers.py:SecurityHeadersMiddleware(app, *, is_production)` adds CSP and
  other browser security headers.
- `rate_limit.py:RateLimit(max_events, window_seconds)` is a frozen dataclass that
  describes one sliding window.
- Limit constants in `rate_limit.py`, each overridable through its `RATE_LIMIT_*_MAX` /
  `RATE_LIMIT_*_WINDOW_SECONDS` env vars (`_int_env`):

  | Constant | Default |
  | --- | --- |
  | `AUTH_LIMIT` | 10/60s |
  | `WEBHOOK_LIMIT` | 60/60s |
  | `MCP_MUTATION_LIMIT` | 30/60s |
  | `MCP_MISS_LIMIT` | 120/60s |
  | `OAUTH_REGISTRATION_LIMIT` | 10/3600s |
  | `PUBLIC_LIMIT` | 120/60s |
  | `USER_API_LIMIT` | 300/60s |
  | `USER_SEARCH_LIMIT` | 30/60s |

- `rate_limit.py:rate_limit_dependency(limit, prefix)` returns a FastAPI dependency
  keyed on `<prefix>:<client ip>`. It raises a 429 `HTTPException`.
- `rate_limit.py:enforce_user_rate_limit(user_id, limit, prefix="user")` is the
  per-user budget. `thestill/web/dependencies.py:user_rate_limit` and
  `user_search_rate_limit` call it.
- `rate_limit.py:enforce_mcp_mutation_quota(tool_name, session_key=None)` uses the
  key `mcp:<session|default>:<tool>` and raises `RateLimitExceeded`.
- `rate_limit.py:check_mcp_token_rate_limit(token_hash, *, requests_per_minute)` returns a bool.
- `rate_limit.py:mcp_miss_budget_exhausted(ip)` and `record_mcp_token_miss(ip)` track the
  per-IP budget for failed MCP token lookups.
- `rate_limit.py:allow_oauth_registration(ip)` and `allow_public_request(request)` return
  bools and never raise.
- `rate_limit.py:resolve_client_ip(request, config=None)` returns the real client IP
  behind trusted proxies.
- `rate_limit.py:TrustedProxies` / `trusted_proxy_set(config)` match exact strings and
  CIDR ranges from `config.trusted_proxies`. The set is cached with `lru_cache(maxsize=16)`.
- `rate_limit.py:reset_for_testing()` clears `_LIMITER` and `USER_HITS`.

## Invariants

- `body_size.py:BodySizeLimitMiddleware.dispatch` checks only POST, PUT and PATCH. When
  route prefixes overlap, the longest one wins (`__init__` sorts them by length).
  - A suffix match (`_suffix_limit_for`, on the path without a trailing `/`, longest
    suffix first) wins over any prefix match.
  - A non-integer `Content-Length` gets a JSON 400. An oversized one gets a JSON 413.
  - A missing `Content-Length` (chunked upload) is not capped on a prefix or default
    limit. On a path with a suffix limit it gets a JSON 411, because the cap could not
    otherwise be applied before the body is read.
  - It returns `JSONResponse` directly because an `HTTPException` raised inside
    `BaseHTTPMiddleware` is not converted.
- `rate_limit.py:resolve_client_ip` reads `X-Forwarded-For` only when the direct peer is
  a trusted proxy.
  - It walks the header right to left and returns the first untrusted hop.
  - It falls back to the peer, or `"unknown"` when there is no client.
  - A host that is not a string is never trusted (`TrustedProxies`).
- `rate_limit.py:_SlidingWindow.allow` refuses once the live count reaches `max_events`,
  and a refused call adds no event. `exhausted()` never adds an event. The window uses
  `time.monotonic` behind a `threading.Lock`. Stale buckets are swept once there are
  more than `_SWEEP_TRIGGER` (1024).
- The limiter is per process and kept in memory: with N workers the effective limit is N
  times the configured value (`rate_limit.py` module docs).
- The 429s from `rate_limit_dependency` and `enforce_user_rate_limit` carry a
  `Retry-After` equal to the window length.
- Env limits are read once, at import (`rate_limit.py:_int_env`). An unparsable value
  falls back to the default.
- `check_mcp_token_rate_limit` logs only the first 8 characters of a token hash.
  `logging_middleware.py:_safe_endpoint` and `body_size.py` log paths through
  `redact_capability_path`, so `/mcp/{secret}` appears as `/mcp/<redacted>`. Query
  params are logged through `redact_mapping`.
- `security_headers.py:SecurityHeadersMiddleware.dispatch` never overwrites a header the
  route already set. It sends HSTS (`max-age=31536000; includeSubDomains`) only when
  `is_production` is set.
- `security_headers.py:_CSP` sets `frame-ancestors 'none'` and allows YouTube only in
  `script-src` and `frame-src`.

## Side Effects

- `rate_limit.py:_LIMITER` is a module-global, in-memory dict of deques.
  `enforce_user_rate_limit` also calls `services.abuse.rate_limit_hits.USER_HITS.record`
  when a user goes over their limit.
- `logging_middleware.py:LoggingMiddleware.dispatch`:
  - binds the structlog contextvars `request_id` (8 hex characters of a uuid4),
    `method`, `endpoint` and `client_ip`, and clears them in `finally`;
  - logs `http_request_started` and `http_request_completed` (warning for 4xx, error
    for 5xx), or `http_request_failed` with `exc_info`;
  - sets the `X-Request-ID` response header.
- `security_headers.py:SecurityHeadersMiddleware.dispatch` sets these headers:
  - `Content-Security-Policy`
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - `Strict-Transport-Security`, in production only
- Warning log events:
  - `rate_limit.py`: `rate_limit_exceeded`, `mcp_mutation_quota_exceeded`,
    `mcp_token_rate_limit_exceeded`, `oauth_registration_limit_exceeded` and
    `mcp_token_miss_budget_exhausted`
  - `body_size.py`: `body_size_content_length_not_int` and `body_size_cap_exceeded`

## Verification

- Run: `./venv/bin/pytest tests/unit/web tests/integration/web` (full suite: `make test`)
