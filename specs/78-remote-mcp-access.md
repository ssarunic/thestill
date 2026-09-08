# Remote MCP Access — Streamable HTTP on the Web Server

**Status**: 🚧 Phase 1 + Phase 2 implemented on `feat/78-remote-mcp-access` (2026-09-08); pending manual connector test + merge. `MCP_HTTP_SECRET` no longer exists — Phase 1's operator URL was superseded before it ever shipped
**History**: Originally drafted as spec #71 on the unmerged branch `claude/mcp-web-server-integration-70yky4` (commit `48912ff`, 2026-08-08); revived and renumbered on 2026-09-08 after #71 was taken by player-shell-layer
**Created**: 2026-08-08
**Updated**: 2026-09-08 (Phase 2 designed)
**Priority**: Medium (unlocks Claude mobile / claude.ai custom connectors; today MCP is desktop-stdio only)
**Builds on**: [30-mcp-anchors-and-entity-discovery.md](30-mcp-anchors-and-entity-discovery.md), [25-security-audit-and-hardening.md](25-security-audit-and-hardening.md), [06-authentication.md](06-authentication.md)

## Overview

Thestill's MCP server (`thestill-mcp`) speaks stdio only. That works for
Claude Desktop, which spawns the process locally, but Claude on mobile and
claude.ai custom connectors cannot run local processes — they connect to a
**remote MCP server** over the **Streamable HTTP** transport, from
Anthropic's infrastructure, against a publicly reachable HTTPS URL.

`thestill/mcp/server.py` anticipated this ("When migrating to HTTP/SSE
transport, use LoggingMiddleware…"). This spec executes the migration
without forking the tool surface: the same `Server` instance built by
`setup_tools`/`setup_resources` is mounted into the existing FastAPI app
behind a new endpoint, so the stdio and HTTP transports stay two doors
into one room.

Auth is deliberately phased:

- **Phase 1 (this spec's deliverable): capability URL.** The endpoint is
  mounted at `/mcp/{secret}` where the secret is an operator-configured
  high-entropy string. Possession of the URL is the credential. The URL is
  surfaced (admin-only) in the web Settings page for copy-paste into
  claude.ai's custom-connector form.
- **Phase 2: per-user capability URLs.** The one operator secret becomes
  a token per user, stored hashed, minted from each user's own Settings
  page (shown once, rotatable). The guard resolves the path token to a
  user and tools inherit that identity; a token is equivalent to that
  user's web session — same reads, same follow semantics, admin-only
  pipeline. Individually managed Claude accounts only. Decided
  2026-09-08, revised the same day after review; OAuth 2.1 becomes
  Phase 3, triggered by organisation connectors (see §Phase 2 ▸
  Deferred).

## Customer outcomes

- **O1 — "Ask my podcasts from my phone."** A self-hosting user adds
  their thestill instance as a custom connector on claude.ai once, and
  every Claude surface they use (mobile, web, desktop) can call
  `search_corpus`, `find_mentions`, `get_entity`, the pipeline tools, etc.
- **O2 — zero new tool code.** The remote surface exposes exactly the
  tools/resources the stdio server exposes; spec #30's tools arrive on
  mobile for free.

## Constraints and threat model (Phase 1)

- **The capability URL is operator-equivalent.** The MCP tool surface has
  no per-user identity (same as stdio today) and includes mutating
  pipeline tools. Whoever holds the URL can do anything the instance can
  do. Therefore:
  - The secret is **explicit, operator-supplied, and fail-fast** —
    matching the `JWT_SECRET_KEY` precedent from spec #25 item 4.1. No
    auto-generation: a silently regenerated secret would silently break
    the connector, and "works but quietly broken" is the failure mode
    specs #25/#51 exist to kill.
  - The Settings surface that displays the URL is **admin-gated**
    (`require_admin`); in single-user mode that always passes.
  - Ship dark *(Phase 1 only — Phase 2 flipped the default to `true`, see
    §Phase 2 ▸ Configuration)*: `MCP_HTTP_ENABLED` defaults to `false`. Hosted multi-user
    deployments should leave Phase 1 off unless the operator accepts that
    the URL is an admin credential; Phase 2 is the multi-user answer.
- **Transport-level secrecy is assumed.** The capability URL must only
  travel over HTTPS. Path components appear in access logs; thestill's own
  `LoggingMiddleware` must redact the secret path segment (log
  `/mcp/<redacted>`), **and so must uvicorn's access logger**, which
  formats the raw ASGI path and bypasses structlog (a live boot on
  2026-09-08 showed the secret in every `uvicorn.access` line while the
  middleware line was clean). `create_app` attaches a `logging.Filter`
  to `uvicorn.access` when the endpoint is mounted. Operators fronting
  with a reverse proxy own their proxy's log hygiene.
- **Timing**: the secret comparison uses `secrets.compare_digest`.
- **No CORS exposure**: claude.ai connects server-side, not from a
  browser; `/mcp` is not added to any CORS allowance.

## Design

### Configuration

| Env var | Default | Meaning |
|---|---|---|
| `MCP_HTTP_ENABLED` | `true` (since Phase 2; was `false` in Phase 1) | Mount the Streamable HTTP MCP endpoint on the web server; inert until a user mints a token |
| `MCP_HTTP_SECRET` | — | Capability secret, **required when enabled**, min 32 chars. Generate with `openssl rand -hex 32` |

Validation lives in `load_config()` next to the `COOKIE_SECURE` /
`MULTI_USER` checks: enabled-without-secret (or a short secret) raises at
boot with the one-line remediation.

### Transport mount

- New module `thestill/web/mcp_http.py` exposing
  `build_mcp_http(config) -> McpHttpRuntime | None`:
  - Builds the same low-level `mcp.server.Server` via the existing
    `setup_resources`/`setup_tools` (which construct their own
    repositories from config — acceptable duplication for Phase 1; a
    follow-up may thread `AppState` through instead).
  - Wraps it in the SDK's `StreamableHTTPSessionManager` with
    `stateless=True` (no session resumption, no sticky state — safe
    behind load balancers, and Claude's client handles stateless servers)
    and `json_response=True` (plain JSON responses; no SSE stream
    needed in stateless mode).
  - Returns an ASGI guard app that constant-time-compares the first path
    segment under the mount against the secret; wrong or missing secret →
    404 (indistinguishable from "no such route"). On match it forwards to
    `session_manager.handle_request`.
- `create_app()` mounts the guard at `/mcp` **before** the static/SPA
  registration and enters `session_manager.run()` inside the existing
  lifespan (via `AsyncExitStack`), so the manager's task group lives and
  dies with the app.
- The SPA catch-all's skip list gains `"mcp/"` as defense in depth.
- `pyproject.toml` bumps the floor to `mcp>=1.8.0` (first release with
  `StreamableHTTPSessionManager`).
- The per-session mutation quota in `mcp/tools.py` keys off process
  identity today; under HTTP all connector traffic shares one key. That is
  *stricter*, not looser — acceptable for Phase 1, revisit in Phase 2.

### Settings surface

- `GET /api/status/mcp` on the existing `api_status` router (already
  mounted with `require_admin`). Response:

  ```json
  { "status": "ok", "mcp": { "enabled": true,
      "url": "https://host/mcp/<secret>", "transport": "streamable-http" } }
  ```

  The URL base is `PUBLIC_BASE_URL` when set, else the request's own base
  URL. When disabled: `{ "mcp": { "enabled": false } }`.
- Frontend: a "Claude connector (MCP)" card on the Settings page, rendered
  only for admins (`isAdmin` from `AuthContext`). Shows the URL with a
  copy button and one-line instructions ("claude.ai → Settings →
  Connectors → Add custom connector"); when disabled, shows the env vars
  needed to enable it. The URL is masked by default (click to reveal) so
  a screen-share doesn't leak the credential.

### Explicitly out of scope for Phase 1

- Per-user tokens and per-user tool scoping over HTTP (Phase 2).
- OAuth 2.1 / protected-resource metadata (deferred beyond Phase 2).
- SSE legacy transport (deprecated upstream; not implemented).
- Rotating the secret from the UI (operator rotates the env var; the
  Settings card reflects whatever is configured).

## Phase 2 — per-user capability URLs

**Status**: ✅ Implemented 2026-09-08 on `feat/78-remote-mcp-access` (clean-architecture
variant: `McpTokenService`, `mcp/scopes.py`, `mcp/identity.py`,
`ConfirmDialog`). Automated acceptance green on SQLite and a real
Postgres; the two-account manual test is still open.

### Why per-user, not OAuth

claude.ai's custom-connector form for an *individually managed* account
accepts a raw remote MCP URL with OAuth optional, so a path token is the
only non-OAuth credential. Phase 1 proved the transport; the missing
piece is *identity*, and a per-user token buys identity and revocation
without standing up an authorization server.

Two things this is **not**, stated so nobody mistakes it for more:

- **Not standards-based MCP authorization.** The MCP authorization spec
  uses bearer tokens in the `Authorization` header with 401/403
  semantics and protected-resource metadata. This is Claude-specific
  capability authentication that happens to work because claude.ai
  accepts a bare URL. Any client that follows the spec strictly will not
  use it; that is the Phase 3 trigger.
- **Not for Team / Enterprise connectors.** An organisation owner
  registers *one* server URL centrally and members connect their own
  accounts afterwards; there is no place to supply a different path per
  member. Phase 2 is explicitly limited to individually managed Claude
  accounts. Organisation deployments need OAuth (Phase 3).

### Authorization boundary — one rule

**A user's web session is the ceiling; a token is a scoped subset of
it.** Nothing a token can do exceeds what its owner can do signed in on
thestill.me, and the token's *scopes* (next section) narrow that
further — read-only by default. The ceiling, matching what the web
routes do today:

| Surface | Authenticated user | Admin |
|---|---|---|
| Podcast list (`list_podcasts`) | Own follows only, like the web Podcasts page | Same, plus `all=true` |
| Podcast / episode / transcript / summary / audio reads — tools **and** `thestill://` resources | Any podcast in the instance (the web episode and transcript routes are corpus-wide for any signed-in user) | Same |
| Search, mentions, quotes, entities | Corpus-wide, like the web search page | Same |
| `add_podcast` | Add + auto-follow the caller (spec #63 companion path) | Same |
| `remove_podcast` | **Unfollow.** Never deletes; refused if the caller does not follow it | Same (delete stays CLI-only) |
| Pipeline tools (`refresh_feeds`, `download_episodes`, `downsample_audio`, `transcribe_episodes`, `clean_transcripts`, `process_episode`, `summarize_episodes`) | Hidden from `tools/list`, refused on `tools/call` | Allowed |
| `get_status` | **Reduced payload**: counts over the caller's follows only, no `storage_path`, no provider or system-wide numbers. The web `/api/status` it mirrors is admin-gated, so the full payload is *above* a user session | Full operator payload as today |

Consequences stated plainly:

- **A leaked read-only URL exposes the corpus**, as a leaked session
  cookie does, but nothing else: it cannot add a podcast (a pipeline
  trigger in disguise), cannot change follows, cannot run the pipeline,
  cannot delete, is rate-limited, expires, is revocable per user, and its
  use is visible on the card. It is *not* a follows-only view; the earlier draft
  claimed that and was wrong.
- If the instance ever decides that transcripts are private to followers,
  that is a **web** decision first (spec #63 follow-up), and MCP inherits
  it through the same service calls. Phase 2 does not introduce an
  authorization model the web does not have.
- Resources are covered by the same rule, not just tools. `resources/list`
  returns the static URI templates; `resources/read` resolves the podcast
  and applies the read row above. Acceptance tests exercise
  `resources/read` over HTTP, not only `tools/call`.

### Token scopes

Scopes are stored on the token row and chosen on the Settings card at
create / rotate time. They only ever narrow the session ceiling above.

| Scope | Grants | Default | Offered to |
|---|---|---|---|
| `read` | `list_podcasts`, `list_episodes`, `get_status`, `get_transcript`, `get_summary`, `get_episode_clip`, `search_corpus`, `find_mentions`, `list_quotes_by`, `get_entity`, `list_episodes_by_entity`, all `thestill://` resources | on, cannot be removed | everyone |
| `follows` | `add_podcast` (add + auto-follow), `remove_podcast` (unfollow) | **off** | everyone |
| `pipeline` | `refresh_feeds`, `download_episodes`, `downsample_audio`, `transcribe_episodes`, `clean_transcripts`, `process_episode`, `summarize_episodes` | off | admins only; silently dropped for non-admins |

- `tools/list` returns only the tools the token's scopes grant, so Claude
  never offers a tool that would be refused. `tools/call` re-checks (the
  list is advisory; the check is the control) and refuses with an error
  naming the missing scope.
- `add_podcast` sits in `follows`, not `read`, deliberately: adding a
  feed enqueues download, transcription and summarisation, so it is the
  one "read-looking" tool that spends money.
- Scope checks live next to the existing `_MUTATING_TOOLS` quota check in
  `call_tool`; a `_SCOPE_BY_TOOL` mapping replaces the ad-hoc set.
  **Fail closed**: a tool with no entry is omitted from `tools/list` and
  refused on `tools/call` over HTTP, and a contract test asserts every
  tool registered in `list_tools` has an explicit scope, so a future
  tool cannot become remotely reachable by omission.
- **`is_admin` is re-checked per request**, not at mint time. The guard
  loads the user row fresh on every request; `pipeline` (and the full
  `get_status` payload) is effective only while `user.is_admin` is true
  *now*. Demoting an admin disables their existing `pipeline` token on
  the next request, with no rotate needed.
- stdio is unscoped (it is already local process access).
- Scopes are fixed for a token's life; changing them is a rotate, so the
  URL in claude.ai always reflects what it can do.

### Rate limit, expiry, last use

Detective and bounding controls that apply to every token regardless of
scope:

- **Per-token HTTP request limit.** `MCP_TOKEN_REQUESTS_PER_MINUTE`
  (default `120`), enforced in the guard with the existing sliding-window
  limiter from `web/middleware/rate_limit.py`, keyed on the token hash.
  This is deliberately an *HTTP* limit, not a read-call quota: the guard
  runs before the SDK parses JSON-RPC, so it counts `initialize`,
  `tools/list`, notifications and calls alike and cannot see the method
  without consuming the body. Over the limit → plain `429` with a
  `Retry-After` header and an empty body; claude.ai's client surfaces
  that as a transient connector error. The JSON-RPC
  `retry_after_seconds` shape stays where it is today, on the mutation
  quota inside `call_tool`. The purpose is to make a bulk export of the
  corpus through a leaked URL slow and visible, not to police normal chat
  use; one Claude turn is a handful of requests.
- **Expiry.** `expires_at = created_at + MCP_TOKEN_TTL_DAYS` (default
  `90`, `0` disables). The guard treats an expired token like a revoked
  one (404). The card shows the date and a warning in the last 14 days;
  rotating resets it. Bounds how long a forgotten paste stays live.
- **Last use.** `last_used_at` and `last_used_ip` (via
  `resolve_client_ip`, trusted-proxy aware) are bumped at most once per
  minute and shown on the card. The *timestamp* is what lets a user spot
  use they did not make. The IP is **diagnostic, not attribution**: for
  normal connector traffic it is an Anthropic egress address, so it
  distinguishes "claude.ai called this" from "something else called
  this" and no more; it cannot say who held a stolen URL. Display only,
  never logged with the token.

### Hardening (post-implementation review, 2026-09-08)

Four findings from the implementation review, all fixed on the branch:

- **Remote calls run off the event loop.** The tool handlers are
  synchronous and some (transcribe, process_episode) run for hours. Over
  HTTP they were awaited directly on uvicorn's loop, freezing health
  checks, the API and token revocation. `call_tool` and `read_resource`
  now dispatch remote callers to a worker thread (`anyio.to_thread`) on a
  **dedicated per-server `CapacityLimiter`** (`REMOTE_CALL_WORKERS = 4`),
  never AnyIO's default thread limiter — that is the pool Starlette runs
  synchronous API routes on, so sharing it would let forty long MCP calls
  starve token rotation and revocation. Calls beyond the limit queue; the
  per-token HTTP limit bounds the queue. stdio keeps the direct call.
  Routing pipeline tools through the task queue remains the better
  long-term shape and is noted as a follow-up.
- **Missing identity fails closed.** Only `request is None` means stdio.
  An HTTP request whose scope state lost the guard's user resolves to
  `ANONYMOUS_REMOTE`: no scopes, no tools listed, every call and every
  resource read refused, and a warning logged (with the path redacted
  like every other sink).
- **Every path-logging sink redacts `/mcp/{token}`.** Beyond the two
  access loggers, the body-size middleware's 413/400 log lines and the
  generic unhandled-exception log now go through
  `redact_capability_path`.
- **Config validation.** `MCP_TOKEN_TTL_DAYS` must be `>= 0` (only exactly
  `0` means never expires) and `MCP_TOKEN_REQUESTS_PER_MINUTE` must be
  `> 0`; both fail at boot with the remediation, and the service refuses a
  negative TTL independently.

### Identifiers — resolve, then authorize

Every tool and resource that takes a podcast or episode identifier
follows one rule: **resolve the identifier against the corpus, then
authorize the resolved object for the caller.** Identifiers are
corpus-global (uuid, slug, RSS URL); there is no user-relative numbering.

- `list_podcasts` returns follower-scoped rows but each row carries the
  global `id` and `slug`, so a follow-up `list_episodes` /
  `get_transcript` / `remove_podcast` resolves the same object.
- The legacy 1-based numeric index accepted by `PodcastService.get_podcast`
  is corpus-global and order-dependent. Phase 2 removes it from the MCP
  tool schemas' descriptions ("uuid, slug, or RSS URL") and refuses bare
  integers over HTTP with an error naming the accepted forms. stdio keeps
  accepting it for CLI parity.
- Search keeps its existing single `podcast_id` filter. A `followed_only`
  convenience filter (needs a `podcast_ids` set filter in both search
  backends) is **out of Phase 2**; it is a follow-on once the set filter
  exists, and it is a convenience, not an authorization control.

### Token lifecycle

One row per user in `mcp_tokens`, with a SQLite + Postgres repository
pair per the spec #44 pattern. **The schema lands in three places**,
because the two backends bootstrap differently and Alembic only covers
one of them:

1. `SqlitePodcastRepository` bootstrap — an inline
   `CREATE TABLE IF NOT EXISTS mcp_tokens` next to `revoked_tokens`,
   plus the idempotent-`ALTER` pattern used there for later columns.
   SQLite installs never run Alembic.
2. `postgres_schema.SCHEMA_SQL` — so a fresh Postgres database
   initialised through `ensure_schema` has the table.
3. Alembic `0009_mcp_tokens` — so an existing Postgres database (prod)
   gains it on upgrade. Must be idempotent against a database that
   already got the table from (2).

| Column | Notes |
|---|---|
| `user_id` | PK, FK → `users.id`. One row per user, updated in place |
| `token_hash` | SHA-256 of the token; the plaintext is never stored |
| `token_prefix` | first 6 chars, for the masked Settings display only |
| `scopes` | text, comma-separated subset of `read,follows,pipeline`; `read` always present |
| `created_at`, `expires_at`, `last_used_at`, `revoked_at` | timestamps, UTC; `expires_at` NULL when TTL disabled |
| `last_used_ip` | text NULL, display only |

- Tokens are `secrets.token_hex(32)`, the same entropy as Phase 1.
- **Create / rotate is one `UPSERT` in one transaction**: new hash, new
  prefix, requested scopes, `created_at = now()`, `expires_at`
  recomputed, `revoked_at = NULL`, `last_used_* = NULL`. Because the row is
  replaced in place there is no history and no uniqueness conflict; the
  previous token is dead the moment the transaction commits.
- **Revoke** sets `revoked_at`; the row stays so the card can say "revoked
  on …". Guard lookups require `revoked_at IS NULL`.
- **The plaintext is shown exactly once**, in a modal after create or
  rotate, with a Copy button and the instruction to paste it into
  claude.ai now. There is no Reveal: hash-only storage makes it
  impossible, and that is the point (GitHub personal-access-token
  pattern). A user who lost it rotates.
- `last_used_at` / `last_used_ip` are bumped at most once per minute.

### Guard resolves a user

`McpHttpRuntime` hashes the path segment, looks the hash up (indexed,
`revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())`),
applies the per-token rate limit, and on a hit stashes the `User` and
the token's scopes on `scope["state"]["mcp_user"]` /
`scope["state"]["mcp_scopes"]` before handing the request to the
session manager. Miss, revoked or expired → the same empty 404 as
today. The SDK forwards the
Starlette request into `server.request_context.request`, so tool and
resource handlers read the user from `request.scope["state"]`. A
`current_mcp_user()` helper in `mcp/tools.py` returns that user over
HTTP and **`None` on stdio** (no request). The two doors share one
room, but the handlers branch explicitly on identity so stdio keeps its
legacy semantics rather than inheriting per-user ones by accident:

| Handler | Identity present (HTTP) | Identity `None` (stdio) |
|---|---|---|
| `list_podcasts` | caller's follows | whole corpus, as today |
| `remove_podcast` | unfollow | delete, as today |
| `add_podcast` | add + auto-follow caller | add + auto-follow default user, as today |
| `get_status` | reduced / full by `is_admin` | full, as today |
| numeric podcast index | refused | accepted, as today |
| scopes, rate limit, expiry | enforced | not applicable |

The per-session mutation quota keys on `user_id` over HTTP and keeps
its process key on stdio.

### Settings surface

- The Phase 1 admin-only card becomes a per-user **Claude connector**
  card on the Settings page. States: *none yet* (Create button), *active*
  (masked `…/mcp/<prefix>••••`, scopes as chips, created, expires,
  last-used time and IP, Rotate with confirm, Revoke with confirm),
  *expiring* (same, with a warning inside 14 days), *expired* / *revoked*
  (date + Create again). Create and Rotate open a small form: scope
  checkboxes (`read` locked on, `follows` off, `pipeline` shown to admins
  only) before the one-time URL modal.
- Routes on a user-authenticated namespace, never the admin router:
  - `GET /api/me/mcp-token` → `{ enabled, state, prefix, scopes,
    created_at, expires_at, last_used_at, last_used_ip, revoked_at }` —
    never the plaintext.
  - `POST /api/me/mcp-token` with `{ scopes }` → create or rotate →
    `{ url, scopes, expires_at }` once. Non-admins requesting `pipeline`
    get it dropped, not an error.
  - `DELETE /api/me/mcp-token` → revoke.
- `GET /api/status/mcp` (admin) survives, reporting only whether the
  endpoint is enabled.

### Configuration

- `MCP_HTTP_ENABLED` **defaults to `true`** (decided 2026-09-08, after
  implementation). "Ships dark" was a Phase 1 artifact: the mount was an
  operator-level secret. In Phase 2 the endpoint is inert until a user
  mints a token, and a token can never exceed its owner's web session, so
  enabling the mount is no longer a security decision. The flag stays as
  an opt-out for operators who do not want out-of-band credentials at
  all. Two mitigations make default-on safe:
  - **Minting requires a secure origin.** `POST /api/me/mcp-token` refuses
    (400) unless `PUBLIC_BASE_URL` is `https://…`, or the request itself
    is HTTPS, or the host is loopback. The token rides in the URL, so a
    plain-http self-host must not be able to mint one that leaks in
    transit.
  - **Per-IP token-miss budget.** Unknown/revoked/expired tokens count
    against `RATE_LIMIT_MCP_MISS_MAX` (120/min per client IP). Once burnt,
    the guard answers the same empty 404 *before* the database lookup, so
    scanners cannot make it do work. Generous on purpose: claude.ai's
    egress IPs are shared across users, and one stale connector polling a
    revoked URL must not lock out its neighbours.
- The HTTP mount receives the app's `Config` (`setup_tools(..., config=)`)
  instead of re-reading the environment, so embedders and tests that build
  a `Config` directly get exactly the instance they configured.
- New: `MCP_TOKEN_TTL_DAYS` (default `90`, `0` = never expires) and
  `MCP_TOKEN_REQUESTS_PER_MINUTE` (default `120`).
- `MCP_HTTP_SECRET` is **removed**, with its boot-time validation.
  Operator migration note: Phase 1 connector URLs stop working on
  upgrade; each user mints their own. Acceptable because Phase 1 shipped
  dark and was single-admin.

### Unchanged

Log redaction (both loggers already collapse `/mcp/*`), transport
(stateless Streamable HTTP, JSON responses), mount order, SPA skip list.

### Out of scope for Phase 2

- **Inbox and briefing tools.** The MCP surface has none today and
  Phase 2 adds no tools; "my inbox from my phone" is a separate spec that
  designs those tools and their (per-user by construction) authorization.
- `followed_only` search filter (see Identifiers).
- Organisation (Team / Enterprise) connectors — Phase 3.

### Deferred (Phase 3 trigger), not rejected

- **OAuth 2.1 with thestill as authorization server.** Required the
  moment an organisation connector or a spec-strict client (ChatGPT
  connectors, an IDE) needs to reach thestill. The guard stays a thin
  ASGI wrapper so a bearer verifier plus
  `/.well-known/oauth-protected-resource` can replace the hash lookup
  without touching the mount or session manager.

### Rejected

- **Signed stateless tokens (the spec #51 unsubscribe pattern).** No
  revocation without a denylist, and rotate is the whole point.
- **Encrypted reversible storage to support Reveal.** Adds a key to
  manage for a convenience the one-time modal covers.
- **Tokens in the query string.** Same secrecy, worse: query strings
  are more likely to be logged by proxies.
- **Follows-only read model over MCP.** Would give MCP an authorization
  model the web does not have; see the one-rule section.
- **IP allowlisting to Anthropic egress ranges.** Not adopted because no
  published, stable range for connector traffic was verified; revisit
  if Anthropic documents one.

### Phase 2 acceptance

- [x] `mcp_tokens` exists on all three bootstrap paths: a fresh SQLite
      file, a fresh Postgres via `ensure_schema`, and an existing Postgres
      via `alembic upgrade` (idempotent when the table is already there);
      rotate is one transaction; the old URL 404s on the very next
      request.
- [x] Guard resolves the user; for two users with different follows,
      `list_podcasts` over HTTP returns each caller's own follows with
      global ids/slugs.
- [x] `get_transcript` via `tools/call` **and**
      `thestill://…/transcript` via `resources/read` succeed for an
      authenticated non-follower (web parity) and 404 without a token.
- [x] `remove_podcast` unfollows, never deletes, and is refused for a
      non-follower; the podcast remains for the other follower.
- [x] Scopes: a `read`-only token lists no mutating tools and is refused
      on `add_podcast` with an error naming `follows`; a `read,follows`
      token can add and unfollow; `pipeline` is dropped from a
      non-admin's request and honoured for an admin.
- [x] Rate limit: the 121st HTTP request within a minute on one token
      returns `429` with `Retry-After`; another user's token is
      unaffected; the mutation quota still returns its JSON-RPC shape.
- [x] Expiry: a token with `expires_at` in the past 404s; rotate resets
      `expires_at`; `MCP_TOKEN_TTL_DAYS=0` yields NULL.
- [x] Last use: `last_used_at` / `last_used_ip` update at most once per
      minute and appear in `GET /api/me/mcp-token`; the IP never appears
      in access logs alongside the path.
- [x] `get_status` for a non-admin token omits `storage_path` and
      system-wide counts; an admin token gets the full payload; demoting
      that admin changes the very next response with no rotate.
- [x] Contract test: every tool in `list_tools` has a `_SCOPE_BY_TOOL`
      entry; a tool registered without one is absent from `tools/list`
      over HTTP and refused on call.
- [x] Bare integer podcast ids refused over HTTP; uuid, slug and RSS URL
      accepted and resolve to the same object as `list_podcasts` reports.
- [x] Settings card: create with scope form, one-time copy modal,
      rotate, revoke, expiring warning; the plaintext appears only in
      the `POST` response.
- [x] Access logs, the body-size and unhandled-exception logs, and
      `GET /api/me/mcp-token` never contain the plaintext.
- [x] stdio `thestill-mcp` takes the identity-`None` branch everywhere:
      `list_podcasts` returns the whole corpus, `remove_podcast` deletes,
      numeric ids are accepted, all tools are listed, no scope or rate
      checks run. Asserted by tests that run the same handlers with no
      request in context.
- [ ] Manual: two claude.ai accounts, two thestill users, each sees its
      own podcast list from Claude mobile; both can read any transcript.

## Phase 1 acceptance

> Superseded: Phase 2 replaced the operator secret before Phase 1 was
> field-tested. The transport/guard items below were carried into the
> Phase 2 tests (`tests/unit/web/test_mcp_http.py`); the secret-specific
> ones no longer apply.

- [ ] `MCP_HTTP_ENABLED=true` + valid secret: `initialize` + `tools/list`
      JSON-RPC round trip succeeds over HTTP at `/mcp/<secret>`.
- [ ] Wrong secret → 404; disabled → 404; SPA catch-all never swallows
      `/mcp/*`.
- [ ] Enabled without secret (or short secret) fails at boot with
      remediation text.
- [ ] `GET /api/status/mcp` admin-gated; returns URL derived from
      `PUBLIC_BASE_URL`.
- [ ] Settings page shows the connector card to admins only; copy works.
- [x] Access logs never contain the secret — both `LoggingMiddleware` and `uvicorn.access` (verified with a live boot + curl, 2026-09-08).
- [ ] Manual: add as claude.ai custom connector (via HTTPS tunnel) and
      call `search_corpus` from Claude mobile.
