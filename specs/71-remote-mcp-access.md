# Remote MCP Access — Streamable HTTP on the Web Server

**Status**: 🚧 Phase 1 in progress
**Created**: 2026-08-08
**Updated**: 2026-08-08
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
- **Phase 2 (later, gated on Phase 1 field testing): OAuth 2.1.** The MCP
  authorization spec's protected-resource-metadata + authorization-server
  flow, bridged to the existing `AuthService` users, so claude.ai's
  connector OAuth flow signs a real per-user session. Not designed here
  beyond the boundary notes below.

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
  `/mcp/<redacted>`), and operators fronting with a reverse proxy own
  their proxy's log hygiene.
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

- OAuth 2.1 / protected-resource metadata (Phase 2).
- Per-user tool scoping over HTTP (Phase 2 — requires identity).
- SSE legacy transport (deprecated upstream; not implemented).
- Rotating the secret from the UI (operator rotates the env var; the
  Settings card reflects whatever is configured).

## Phase 2 sketch (not now)

Boundary notes so Phase 1 doesn't paint us into a corner:

- Phase 1's guard is a thin ASGI wrapper; Phase 2 replaces the guard with
  a bearer-token verifier plus `/.well-known/oauth-protected-resource`
  metadata, keeping the same mount and session manager.
- The authorization server can be thestill itself (bridging
  `AuthService`/JWT) or an external IdP; dynamic client registration is
  what claude.ai's connector flow expects.
- Once identity exists, tools gain a user context and the follower-scoped
  read model (spec #63) applies to MCP reads.

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
- [ ] Access logs never contain the secret.
- [ ] Manual: add as claude.ai custom connector (via HTTPS tunnel) and
      call `search_corpus` from Claude mobile.
