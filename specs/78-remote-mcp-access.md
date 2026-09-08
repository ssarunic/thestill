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
  a token per user, stored hashed, shown on each user's own Settings page
  with reveal / copy / rotate. The guard resolves the path token to a
  user, tools inherit that identity, and reads follow the follower-scoped
  model (spec #63). Decided 2026-09-08; replaces the earlier OAuth 2.1
  sketch, which is kept only as a later option (see §Phase 2 ▸ Rejected).

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

**Status**: 📝 Designed 2026-09-08, not started. Gated on the Phase 1
manual connector test passing.

### Why per-user, not OAuth

claude.ai's custom-connector form accepts a URL or an OAuth flow and
nothing else, so a path token is the only non-OAuth credential. Phase 1
proved the transport; the missing piece is *identity*, and a per-user
token buys identity, revocation and follower-scoped reads without
standing up an authorization server. The threat model is unchanged (the
token still travels in the URL, over HTTPS only), but a leaked URL now
exposes one user's follows instead of the whole instance.

### Customer outcomes

- **O3 — "My connector, my podcasts."** Each user opens Settings, reveals
  their own connector URL, pastes it into claude.ai once, and every Claude
  surface sees *their* followed podcasts, inbox and briefings.
- **O4 — rotate without an operator.** A user who pasted the URL into the
  wrong place rotates it themselves; the old URL 404s immediately.

### Storage

New table `mcp_tokens`, one row per user (Alembic `0009`, SQLite +
Postgres repository pair per the spec #44 pattern):

| Column | Notes |
|---|---|
| `id` | uuid |
| `user_id` | FK → `users.id`, unique (one live token per user) |
| `token_hash` | SHA-256 of the token; the plaintext is never stored |
| `token_prefix` | first 6 chars, for the masked Settings display only |
| `created_at`, `last_used_at`, `revoked_at` | timestamps, UTC |

- Tokens are 32 random bytes, hex-encoded (`secrets.token_hex(32)`), the
  same entropy as Phase 1's operator secret.
- Rotation = revoke the old row, insert a new one. The plaintext is
  returned exactly once, by the rotate call, and shown in the card.
- `last_used_at` is bumped at most once per minute (one write per
  connector session, not per tool call).

### Guard resolves a user

`McpHttpRuntime` stops comparing against one value. It hashes the path
segment, looks the hash up (`revoked_at IS NULL`), and on a hit stashes
the `User` on `scope["state"]["mcp_user"]` before handing the request to
the session manager. Miss → the same empty 404 as today. Lookup by
indexed hash keeps the check constant-time in the token, not linear in
the user count.

The MCP SDK already forwards the Starlette request into
`server.request_context.request`, so tool handlers read the user from
`request.scope["state"]`. A small `current_mcp_user()` helper in
`mcp/tools.py` returns that user, or the single-user default user when
there is no request (stdio transport). The stdio and HTTP servers stay
two doors into one room.

### Tools learn who is calling

Services are built once at mount time; identity is threaded per call.

| Tool | Phase 2 behaviour |
|---|---|
| `list_podcasts`, `list_episodes`, `get_status` | Follower-scoped: `FollowerService.get_followed_podcasts(user_id)` and the counts derived from it |
| `add_podcast` | `add_podcast_and_auto_follow` for the caller (spec #63 companion path) |
| `remove_podcast` | **Becomes unfollow.** Deleting a shared podcast from one user's phone is not acceptable; the destructive delete stays CLI/admin-only |
| `get_transcript`, `get_summary`, `get_episode_clip` | Allowed for any episode of a followed podcast; 404-style refusal otherwise |
| `search_corpus`, `find_mentions`, `list_quotes_by`, `get_entity`, `list_episodes_by_entity` | Corpus-wide by default, with a `followed_only` argument (default `true`) that restricts hits to the caller's follows. Corpus-wide stays available so cross-podcast discovery is not lost |
| `refresh_feeds`, `download_episodes`, `downsample_audio`, `transcribe_episodes`, `clean_transcripts`, `process_episode`, `summarize_episodes` | **Admin-only.** Hidden from `tools/list` for non-admins and refused on `tools/call` with a clear error. Single-user mode's default user is admin, so nothing changes there |

The per-session mutation quota in `mcp/tools.py` keys on `user_id`
instead of process identity.

### Settings surface

- The Phase 1 admin-only card becomes a per-user **Claude connector**
  card on the Settings page (there is no separate User page; Settings +
  the user menu is where account-level things live). Masked URL showing
  `…/mcp/<prefix>••••`, Reveal, Copy, Rotate (with confirm), and the
  last-used time so a user can tell whether the connector is alive.
- Routes move off the admin status router onto a user-authenticated
  namespace:
  - `GET /api/me/mcp-token` → `{ enabled, prefix, created_at,
    last_used_at, url_template }` (never the plaintext)
  - `POST /api/me/mcp-token/rotate` → `{ url }` (plaintext, once)
  - `DELETE /api/me/mcp-token` → revoke without replacement
- First visit with no row: the card offers **Create connector URL**,
  which is the rotate call. No token is minted for users who never ask.
- `GET /api/status/mcp` (admin) survives, reporting only whether the
  endpoint is enabled.

### Configuration

- `MCP_HTTP_ENABLED` stays and still ships dark.
- `MCP_HTTP_SECRET` is **removed**, together with its boot-time
  validation. Migration note for operators: any connector configured with
  the Phase 1 URL stops working on upgrade; each user re-adds their own.
  This is acceptable because Phase 1 shipped dark and was single-admin.

### Unchanged

- Log redaction already collapses everything under `/mcp/` in both
  loggers; per-user paths need nothing new.
- Transport (stateless Streamable HTTP, JSON responses), mount order, SPA
  skip list.

### Rejected

- **OAuth 2.1 with thestill as authorization server.** Correct long-term
  answer for third-party clients, but claude.ai's connector flow needs
  dynamic client registration, protected-resource metadata and an
  authorization UI — a project on its own for one client. Revisit when a
  second client (ChatGPT connectors, an IDE) needs it; the guard is still
  a thin ASGI wrapper and a bearer verifier can replace the hash lookup.
- **Signed stateless tokens (the spec #51 unsubscribe pattern).** No
  revocation without a denylist, and "rotate" is the whole point.
- **Tokens as query string instead of path.** Same secrecy properties,
  worse: query strings are more likely to be logged by proxies.

### Phase 2 acceptance

- [ ] `mcp_tokens` migration applies on SQLite and Postgres; rotate
      revokes the old row and the old URL 404s within the same request
      cycle.
- [ ] Guard resolves the user; `list_podcasts` over HTTP returns only the
      caller's follows for two users with different follows.
- [ ] `remove_podcast` unfollows and never deletes; the podcast remains
      for the other follower.
- [ ] Pipeline tools absent from `tools/list` for a non-admin, refused on
      call; present for an admin.
- [ ] Settings card: create, reveal, copy, rotate, revoke; plaintext only
      ever appears in the rotate response.
- [ ] Access logs and the `GET /api/me/mcp-token` response never contain
      the plaintext.
- [ ] stdio `thestill-mcp` behaves exactly as before (default user).
- [ ] Manual: two claude.ai accounts, two users, each sees only their own
      podcasts from Claude mobile.

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
