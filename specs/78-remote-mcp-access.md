# Remote MCP Access — Streamable HTTP on the Web Server

**Status**: 🚧 Phase 1 implemented on `feat/78-remote-mcp-access` (2026-09-08); pending manual connector test + merge
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
  - Ship dark: `MCP_HTTP_ENABLED` defaults to `false`. Hosted multi-user
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
| `MCP_HTTP_ENABLED` | `false` | Mount the Streamable HTTP MCP endpoint on the web server |
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

**Status**: 📝 Designed 2026-09-08, revised the same day after review
(six P1 findings resolved below). Not started; gated on the Phase 1
manual connector test passing.

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

**An MCP token is equivalent to its user's web session.** Whatever a
signed-in user can read or do on thestill.me, the same user can read or
do through the connector, and nothing more. Concretely, matching what
the web routes do today:

| Surface | Authenticated user | Admin |
|---|---|---|
| Podcast list (`list_podcasts`) | Own follows only, like the web Podcasts page | Same, plus `all=true` |
| Podcast / episode / transcript / summary / audio reads — tools **and** `thestill://` resources | Any podcast in the instance (the web episode and transcript routes are corpus-wide for any signed-in user) | Same |
| Search, mentions, quotes, entities | Corpus-wide, like the web search page | Same |
| `add_podcast` | Add + auto-follow the caller (spec #63 companion path) | Same |
| `remove_podcast` | **Unfollow.** Never deletes; refused if the caller does not follow it | Same (delete stays CLI-only) |
| Pipeline tools (`refresh_feeds`, `download_episodes`, `downsample_audio`, `transcribe_episodes`, `clean_transcripts`, `process_episode`, `summarize_episodes`) | Hidden from `tools/list`, refused on `tools/call` | Allowed |
| `get_status` | Allowed | Allowed |

Consequences stated plainly:

- **A leaked URL exposes the corpus**, exactly as a leaked session cookie
  does. The gain over Phase 1 is that it cannot run the pipeline, cannot
  delete, is revocable per user, and is attributable. It is *not* a
  follows-only view; the earlier draft claimed that and was wrong.
- If the instance ever decides that transcripts are private to followers,
  that is a **web** decision first (spec #63 follow-up), and MCP inherits
  it through the same service calls. Phase 2 does not introduce an
  authorization model the web does not have.
- Resources are covered by the same rule, not just tools. `resources/list`
  returns the static URI templates; `resources/read` resolves the podcast
  and applies the read row above. Acceptance tests exercise
  `resources/read` over HTTP, not only `tools/call`.

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

One row per user in `mcp_tokens` (Alembic `0009`, SQLite + Postgres
repository pair per the spec #44 pattern):

| Column | Notes |
|---|---|
| `user_id` | PK, FK → `users.id`. One row per user, updated in place |
| `token_hash` | SHA-256 of the token; the plaintext is never stored |
| `token_prefix` | first 6 chars, for the masked Settings display only |
| `created_at`, `last_used_at`, `revoked_at` | timestamps, UTC |

- Tokens are `secrets.token_hex(32)`, the same entropy as Phase 1.
- **Create / rotate is one `UPSERT` in one transaction**: new hash, new
  prefix, `created_at = now()`, `revoked_at = NULL`. Because the row is
  replaced in place there is no history and no uniqueness conflict; the
  previous token is dead the moment the transaction commits.
- **Revoke** sets `revoked_at`; the row stays so the card can say "revoked
  on …". Guard lookups require `revoked_at IS NULL`.
- **The plaintext is shown exactly once**, in a modal after create or
  rotate, with a Copy button and the instruction to paste it into
  claude.ai now. There is no Reveal: hash-only storage makes it
  impossible, and that is the point (GitHub personal-access-token
  pattern). A user who lost it rotates.
- `last_used_at` is bumped at most once per minute.

### Guard resolves a user

`McpHttpRuntime` hashes the path segment, looks the hash up (indexed,
`revoked_at IS NULL`), and on a hit stashes the `User` on
`scope["state"]["mcp_user"]` before handing the request to the session
manager. Miss → the same empty 404 as today. The SDK forwards the
Starlette request into `server.request_context.request`, so tool and
resource handlers read the user from `request.scope["state"]`. A
`current_mcp_user()` helper in `mcp/tools.py` returns that user, or the
single-user default user when there is no request (stdio). The stdio
and HTTP servers stay two doors into one room; the per-session mutation
quota keys on `user_id`.

### Settings surface

- The Phase 1 admin-only card becomes a per-user **Claude connector**
  card on the Settings page. States: *none yet* (Create button), *active*
  (masked `…/mcp/<prefix>••••`, created and last-used times, Rotate with
  confirm, Revoke with confirm), *revoked* (date + Create again).
- Routes on a user-authenticated namespace, never the admin router:
  - `GET /api/me/mcp-token` → `{ enabled, state, prefix, created_at,
    last_used_at, revoked_at }` — never the plaintext.
  - `POST /api/me/mcp-token` → create or rotate → `{ url }` once.
  - `DELETE /api/me/mcp-token` → revoke.
- `GET /api/status/mcp` (admin) survives, reporting only whether the
  endpoint is enabled.

### Configuration

- `MCP_HTTP_ENABLED` stays and still ships dark.
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

### Phase 2 acceptance

- [ ] `mcp_tokens` migration applies on SQLite and Postgres; rotate is
      one transaction; the old URL 404s on the very next request.
- [ ] Guard resolves the user; for two users with different follows,
      `list_podcasts` over HTTP returns each caller's own follows with
      global ids/slugs.
- [ ] `get_transcript` via `tools/call` **and**
      `thestill://…/transcript` via `resources/read` succeed for an
      authenticated non-follower (web parity) and 404 without a token.
- [ ] `remove_podcast` unfollows, never deletes, and is refused for a
      non-follower; the podcast remains for the other follower.
- [ ] Pipeline tools absent from `tools/list` for a non-admin and refused
      on call; present and working for an admin.
- [ ] Bare integer podcast ids refused over HTTP; uuid, slug and RSS URL
      accepted and resolve to the same object as `list_podcasts` reports.
- [ ] Settings card: create, one-time copy modal, rotate, revoke; the
      plaintext appears only in the `POST` response.
- [ ] Access logs and `GET /api/me/mcp-token` never contain the
      plaintext.
- [ ] stdio `thestill-mcp` behaves exactly as before (default user,
      numeric ids still accepted).
- [ ] Manual: two claude.ai accounts, two thestill users, each sees its
      own podcast list from Claude mobile; both can read any transcript.

## Phase 1 acceptance

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
