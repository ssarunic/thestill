# OAuth for the Remote MCP Server

> **Status:** 📝 Draft (2026-10-27)
> **Created:** 2026-10-27
> **Updated:** 2026-10-27
> **Priority:** Medium. This decides whether the connector can be listed in the Claude directory and set up without copying a secret URL. Until it ships, spec #108's promotion can reach only the readers who already visit thestill.me.
> **Author:** Product & Engineering
> **Related:** [#78 remote-mcp-access](78-remote-mcp-access.md) (per-user capability URLs; this is its deferred Phase 3), [#96 prompt-injection-hardening](96-prompt-injection-hardening.md) (tool output that reaches a model), [#102 sign-in-page](102-sign-in-page.md) (`/login` and the return path), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md) (ban, suspend, logout everywhere), [#108 claude-connector-page](108-claude-connector-page.md) (the `/claude` page and the connector events).

---

## Executive summary

Today a reader connects Claude to thestill by minting a secret URL in
Settings (`/mcp/<token>`), copying it, and pasting it into Claude's
"Add custom connector" form. Spec #78 chose this because it avoided
running an authorization server. It also named the cost: no
standards-based client can use it, organisations cannot use it, and no
directory can list it.

This spec makes `https://thestill.me/mcp` a standard OAuth-protected
MCP server:

- **thestill becomes its own authorization server.** It issues tokens
  to Claude, ChatGPT, Claude Code and other MCP clients after the reader
  signs in with their existing thestill account and approves a consent
  screen.
- **Setup becomes "paste `thestill.me/mcp`, sign in, approve."** The
  same address works for everyone, so it can go in the Claude
  directory, the MCP Registry and a server card.
- **The authorization model does not change.** A token can never do
  more than its owner's web session, the `read` / `follows` /
  `pipeline` scopes keep their meaning, and ban, suspend and logout
  everywhere revoke it at once.
- **Existing personal URLs keep working** until they expire.

The MCP Python SDK we already run (2.2.0) provides the OAuth endpoints
(`/authorize`, `/token`, `/register`, `/revoke`, metadata) behind one
provider interface. We implement that provider on our users and a new
set of token tables. We also add one thing the SDK lacks: Client ID
Metadata Documents, which Claude prefers over dynamic registration.

## Problem and evidence

- **Setup asks too much.** The reader has to find the Settings card,
  choose scopes, copy a URL that is shown once, and paste it into a
  Claude settings page. Since PostHog went live on 2026-10-17, no
  reader other than the operator has made a tool call (spec #108).
- **No directory can list a per-user URL.** Claude's submission portal
  asks for one `https://` server URL, or a fixed set, or a pattern. For
  "authenticated services" it requires OAuth 2.0 with a real consent
  flow, and every tool must carry a `title` and a `readOnlyHint` or
  `destructiveHint`. None of our 20 tools has annotations today.
- **Standards-based clients cannot connect.** The MCP authorization spec
  expects a bearer token in the `Authorization` header, a `401` with
  `WWW-Authenticate` when it is missing, and protected resource metadata
  (RFC 9728). A secret in the path works in claude.ai only because
  claude.ai accepts a bare URL.
- **Organisations cannot use it.** A Team or Enterprise owner registers
  one URL for everyone. There is nowhere to put a different secret per
  member.

## What Claude requires

From Claude's connector authentication documentation (read 2026-10-27).
These are stricter than the MCP spec in places:

- **A `401` starts sign-in.** It must carry
  `WWW-Authenticate: Bearer resource_metadata="…"`. Claude ignores that
  header on any other status.
- **Protected resource metadata.**
  - `resource` must equal the server URL exactly as the user typed it.
  - Claude uses only the first entry in `authorization_servers`.
- **Client registration, in Claude's order of preference:**
  1. A Client ID Metadata Document (CIMD). Claude uses it only if the
     authorization server metadata has both
     `"client_id_metadata_document_supported": true` and `"none"` in
     `token_endpoint_auth_methods_supported`.
  2. Otherwise Dynamic Client Registration (DCR), which registers a new
     client on every fresh connection.
- **PKCE with S256 on every request.** Advertise
  `"code_challenge_methods_supported": ["S256"]`.
- **Redirect URIs to accept:**
  - claude.ai, Desktop, mobile and Cowork use
    `https://claude.ai/api/mcp/auth_callback`. Anthropic says this may
    move to `https://claude.com/api/mcp/auth_callback`, so accept both.
  - Claude Code uses its own CIMD and a loopback redirect on a port that
    changes each session. Match `http://localhost/callback` and
    `http://127.0.0.1/callback` with the port ignored.
- **Consent screen.** It must show the redirect URI's hostname clearly,
  with an extra warning when the only redirect URIs are loopback.
- **Scopes.** Claude requests the `scope` in the `401`'s
  `WWW-Authenticate` header, or else `scopes_supported` from the
  resource metadata. It adds `offline_access` if the authorization
  server lists it, to get a refresh token.
- **Token endpoint.**
  - Accept `application/x-www-form-urlencoded`.
  - Answer `invalid_grant` for a dead refresh token.
  - Rotate refresh tokens for public clients, returning the new one in
    the same response that retires the old.
  - Answer `invalid_client` to make Claude register again.
  - Claude refreshes on a `401` and up to five minutes before expiry.
- **Time limits.** 10 s for discovery, registration and token
  requests; 30 s for refresh.

## Design

### Shape

thestill is both the **resource server** (`/mcp`) and the
**authorization server** (issuer `https://thestill.me`, from
`PUBLIC_BASE_URL`), on one origin. There is no third-party identity
provider in the OAuth flow. Google sign-in stays what it is today: the
way a reader proves who they are to thestill before approving the
consent screen.

| Path | What | Source |
|---|---|---|
| `/.well-known/oauth-protected-resource/mcp` and `/.well-known/oauth-protected-resource` | RFC 9728 metadata: `resource` = `https://thestill.me/mcp`, `authorization_servers` = [issuer], `scopes_supported` = `read follows pipeline` | SDK `create_protected_resource_routes` |
| `/.well-known/oauth-authorization-server` | RFC 8414 metadata. Adds `client_id_metadata_document_supported: true`, `none` in `token_endpoint_auth_methods_supported`, `S256`, and `offline_access` in `scopes_supported` | SDK `create_auth_routes`, with a metadata override for the CIMD fields the SDK does not set |
| `/authorize`, `/token`, `/register`, `/revoke` | OAuth endpoints | SDK handlers on our provider |
| `/oauth/consent` | Consent page (SPA route) | ours |
| `/mcp` | MCP over Streamable HTTP with a bearer token | the existing session manager, behind a new bearer guard |
| `/mcp/{token}` | The spec #78 personal URLs, unchanged | existing guard |

Phase 0 checks two things before any code is written:

- That the SDK routes mount in our FastAPI app ahead of the SPA
  catch-all. Add their paths to the skip list next to `mcp/`.
- That Caddy passes `/.well-known/*` through. If the root paths clash
  with anything, mount the SDK routes under `/oauth` with a path issuer
  instead. The SDK builds the metadata from the issuer, so this only
  changes the issuer string.

### Client registration

**CIMD (preferred, Claude and Claude Code).**

When `client_id` is an `https://` URL, `get_client()` fetches the
metadata document and validates it. The fetch has hard limits:

- `https` only
- refuse private, loopback and link-local addresses after DNS
  resolution (SSRF)
- 5 s timeout and 64 KB cap
- the document's `client_id` must equal the URL

The result is cached in `oauth_clients` (`source = 'cimd'`) for the
document's `Cache-Control` max-age, capped at 24 h, with the last good
copy kept when a refresh fails. The SDK models the field
(`client_id_metadata_document_supported`) but does not implement it on
the server, so this lives in our provider.

**DCR (fallback; ChatGPT and other clients).**

- `/register` is enabled, with `valid_scopes` = `read follows pipeline
  offline_access` and `default_scopes` = `read offline_access`.
- Registrations are public clients (`token_endpoint_auth_method: none`).
- Rate limit per IP: 10 registrations per hour.
- A daily sweep deletes registrations with no grant that are more than
  7 days old. Claude's docs warn that DCR creates a client per fresh
  connection.

**Redirect URIs.**

- Exact match against the registered list, with one exception:
  `http://localhost` and `http://127.0.0.1` match on any port (RFC 8252
  §7.3, and what Claude Code needs).
- Any other `http://` redirect is refused at registration.
- A request with an unregistered redirect gets an error page from us,
  never a redirect, so `/authorize` cannot be used as an open redirect.

We do not use Anthropic-held client credentials or credentials entered
at connection time. CIMD and DCR cover Claude without sharing a secret.

### Authorization and consent

1. **The client opens `/authorize`.** The parameters are `client_id`,
   `redirect_uri`, `state`, `scope`, `code_challenge` (S256 only;
   `plain` is refused), and `resource` (RFC 8707; it must be the `/mcp`
   URL when present).
2. **The provider parks the request.** `authorize()` stores the request
   in `oauth_pending` with a random 160-bit id and a 10-minute expiry,
   and returns `/oauth/consent#request=<id>`. The id is in the fragment
   so it never reaches a log or a `Referer`, as #102 does for `next`.
3. **The consent page.**
   - If the reader is signed out, it sends them through `/login` with
     `next` set back to the consent page (#102).
   - Signed in, it fetches `GET /api/oauth/pending/{id}`.
   - It then shows:
     - the client's name, and the **redirect hostname** in large type
       (for example "claude.ai")
     - a warning when the redirect is loopback only: "a program on this
       computer"
     - the scope checkboxes from the Settings card: `read` locked on,
       `follows` off, `pipeline` for admins only
     - Approve and Deny
4. **Approve.** `POST /api/oauth/pending/{id}/approve` carries the
   chosen scopes and consumes the pending row. It needs the session
   cookie.
   - The cookie is `SameSite=strict`, so a cross-site form cannot post
     with it. That is the CSRF guard.
   - The same rule means the cookie is **not** sent on the cross-site
     navigation from Claude to `/authorize`. That is why `/authorize`
     only parks the request, and the consent page authenticates through
     its own same-origin API calls. Never through a server-side check
     on the navigation itself.
   - It creates or updates the grant for (user, client), with scopes
     narrowed to what was both requested and approved, using
     `normalize_scopes()` from `McpTokenService`.
   - It issues a single-use authorization code (60 s, hashed) and
     answers with the client's `redirect_uri?code=…&state=…`.
   - Deny answers `error=access_denied`.
5. **Framing.** The consent route sets `X-Frame-Options: DENY` and
   `frame-ancestors 'none'`, so it cannot be clickjacked.
6. **Approve again.** A reader who already granted the same client the
   same scopes still sees the consent screen. There is no silent
   re-approval in Phase 1; it is cheap to add later.

### Tokens

- **Opaque random strings, stored as SHA-256 hashes,** like
  `mcp_tokens`. No JWTs, so revocation is one row update and there is no
  signing key to manage.
- **Access token: 1 hour,** bound to `resource` =
  `https://thestill.me/mcp`. Set `validate_token_resource=True`.
- **Refresh token: issued when `offline_access` is granted.** It lasts
  `MCP_TOKEN_TTL_DAYS` (default 90) from the grant's *last use*, so an
  active connection never expires and an abandoned one does.
- **Refresh tokens rotate on every use.** The new token and the
  retirement of the old one happen in one transaction. Presenting a
  retired refresh token revokes the whole grant (reuse detection) and
  answers `invalid_grant`.
- **Refresh re-checks the account.** It answers `invalid_grant` if the
  user is no longer active, so a suspended account cannot refresh.
- **Scopes are fixed per grant.** Changing them means approving again,
  as rotating does for personal URLs today.

### Resource server

A new bearer guard in `web/mcp_http.py` serves the exact path `/mcp`:

- **No `Authorization: Bearer` header, or an unknown, expired or
  revoked token:** `401` with
  `WWW-Authenticate: Bearer resource_metadata="https://thestill.me/.well-known/oauth-protected-resource/mcp", scope="read offline_access"`.
  Misses count against the existing per-IP `RATE_LIMIT_MCP_MISS_MAX`
  budget.
- **Valid token:**
  - Load the user fresh and run `AccessGuard.ensure_active`.
  - Apply the per-grant rate limit (`MCP_TOKEN_REQUESTS_PER_MINUTE`)
    and touch `last_used_at` / `last_used_ip` on the grant.
  - Set the same scope state the personal-URL guard sets today
    (`STATE_USER`, `STATE_IS_ADMIN`, `STATE_SCOPES`, `STATE_GPC`).
  - Hand the request to the same `StreamableHTTPSessionManager`.
  - Nothing in `mcp/tools.py`, `mcp/scopes.py` or `mcp/identity.py`
    changes, because the handlers read identity only from that state.
- **Scope checks stay where they are.** `tools/list` filters by scope
  and `tools/call` checks again. `pipeline` is effective only while the
  user is an admin *now*.

### Tool annotations

Every tool gets a `title`, and the hints below. This is a directory
requirement, and it also tells any client which calls write.

| Tools | `readOnlyHint` | `destructiveHint` |
|---|---|---|
| The 11 `read` tools (`list_podcasts` … `list_episodes_by_entity`) | true | — |
| `add_podcast` | false | false |
| `remove_podcast` | false | true (it unfollows) |
| The 7 `pipeline` tools | false | false |

A contract test asserts that every tool registered in `list_tools` has
a title and an explicit `readOnlyHint`, next to the existing
every-tool-has-a-scope test.

### Data model (alembic 0025, both backends)

- `oauth_clients`: `client_id` PK, `source` (`cimd` | `dcr`),
  `client_name`, `redirect_uris` (json), `metadata` (json),
  `fetched_at`, `created_at`.
- `oauth_pending`: `id_hash` PK, `client_id`, `redirect_uri`, `state`,
  `scopes`, `code_challenge`, `resource`, `expires_at`.
- `oauth_grants`: `id` PK, `user_id` FK (`ON DELETE CASCADE`),
  `client_id`, `client_name` (snapshot for Settings), `redirect_host`,
  `scopes`, `created_at`, `last_used_at`, `last_used_ip`,
  `revoked_at`. Unique on (`user_id`, `client_id`) while not revoked.
- `oauth_codes`: `code_hash` PK, `grant_id`, `redirect_uri`,
  `code_challenge`, `resource`, `expires_at`, `used_at`.
- `oauth_tokens`: `token_hash` PK, `grant_id`, `kind` (`access` |
  `refresh`), `expires_at`, `replaced_by_hash`, `revoked_at`.

Timestamps are ISO-8601 UTC, as everywhere else. A daily sweep deletes
expired pending rows, codes and access tokens, and refresh tokens
retired more than 30 days ago.

### Account controls and Settings

- **Ban, suspend and logout everywhere** (`account_admin_service`)
  revoke every OAuth grant of the user, next to the existing
  `mcp_tokens.revoke` call. Deleting a user cascades.
- **The Settings card becomes "Claude and other AI apps".**
  - First, the connection address `https://thestill.me/mcp` with a
    copy button and one line: "Paste this into Claude → Settings →
    Connectors → Add custom connector, then sign in."
  - Then **Connected apps:** one row per active grant with the client
    name, redirect host, scopes as chips, connected date, last used
    time, and Disconnect (confirm), which revokes the grant.
  - The personal URL moves under an **Advanced** disclosure, unchanged,
    for clients that cannot do OAuth.
- **API:** `GET /api/me/oauth-grants` and
  `DELETE /api/me/oauth-grants/{id}`, on the user-authenticated
  namespace, never the admin router.
- **CLI:** `thestill user show` lists the user's grants. Revoking a
  grant from the CLI goes through the same service and audit log
  (spec #104).

### Analytics (extends spec #108)

- **`mcp_oauth_granted`:** props `scopes` and `client`. `client` is
  `claude` (claude.ai or claude.com redirect), `claude_code` (Claude
  Code's CIMD URL), `chatgpt` (chatgpt.com or openai.com redirect), or
  `other`.
- **`mcp_oauth_revoked`:** prop `by`, which is `user`, `admin` or
  `reuse`.
- **`mcp_tool_call` over OAuth:** takes `client` from the grant, which
  is reliable. #108's `User-Agent` bucket stays only for personal-URL
  calls.
- Same opt-out and Global Privacy Control rules. No client ids, no
  tokens.

### Legacy personal URLs

- `/mcp/{token}` keeps working, and Settings keeps offering it under
  Advanced.
- Once OAuth has worked in Claude web, desktop, mobile and Claude Code
  for 30 days, Settings stops offering a *new* personal URL by default.
  Existing ones run until they expire (90 days), so they retire on
  their own.
- Removing the route is a separate decision, recorded in #78.

## Phases

### Phase 0: checks (half a day)

- [ ] The SDK routes mount ahead of the SPA catch-all, and Caddy serves
  `/.well-known/*`. Pick root or `/oauth` paths.
- [ ] Confirm ChatGPT's current connector requirements (DCR or CIMD,
  redirect hosts) from OpenAI's docs. Record them here.

### Phase 1: authorization server and bearer `/mcp` (behind `MCP_OAUTH_ENABLED`, default off)

- [ ] Alembic 0025 and repositories for both backends, with contract
  tests on SQLite and Postgres.
- [ ] `ThestillOAuthProvider`: CIMD fetch and cache, DCR, pending
  requests, codes, grants, rotating refresh with reuse detection,
  revoke.
- [ ] Metadata routes with the CIMD and `offline_access` fields.
- [ ] Consent page, its two API routes, framing headers, and the
  signed-out path through `/login`.
- [ ] Bearer guard on `/mcp` with the `401` shape above.
- [ ] Revocation on ban, suspend and logout everywhere. Settings
  Connected apps. `GET`/`DELETE /api/me/oauth-grants`.
- [ ] Sweeps for expired rows and unused DCR clients.
- [ ] Manual test with the flag on in staging, then in prod: claude.ai
  web, Claude Desktop, Claude mobile (connector added on web), Claude
  Code (`claude mcp add --transport http thestill https://thestill.me/mcp`),
  and MCP Inspector.

### Phase 2: make it the default

- [ ] Tool titles and hints, with the contract test.
- [ ] `mcp_oauth_granted` / `mcp_oauth_revoked`, and `client` from the
  grant on `mcp_tool_call`.
- [ ] Flip `MCP_OAUTH_ENABLED` on. The Settings card leads with the
  address. The `/claude` page (#108) setup steps become "paste
  `thestill.me/mcp`, sign in, approve".
- [ ] Privacy policy: name connected apps, what a grant can read, how
  to disconnect, and that thestill does not see the conversation.

### Phase 3: listings

- [ ] **Claude directory** via `claude.ai/directory/manage` (any paid
  Claude plan). Have ready:
  - docs URL `/claude`
  - privacy policy URL
  - support contact
  - icon
  - a **reviewer test account**. thestill only offers Google sign-in,
    so this is a dedicated Google account with follows and read
    history, used for nothing else.
  - answers for the seven compliance acknowledgments (including
    prompt injection, spec #96, and conversation data: none collected)
- [ ] **The official MCP Registry entry, a server card and an
  `ai-catalog.json`.** The server-card proposal's status was unclear
  on 2026-10-27, so check it then.
- [ ] Optional: Smithery, PulseMCP, mcp.so, awesome-mcp-servers.

## Acceptance

- `curl -i https://thestill.me/mcp` answers `401` with the
  `resource_metadata` header. Both metadata documents validate. The
  protected resource `resource` field is exactly
  `https://thestill.me/mcp`.
- **Claude, using CIMD.** A new reader pastes `thestill.me/mcp` into
  claude.ai, signs in with Google, approves `read`, and calls
  `list_podcasts`, which returns their follows. The grant shows in
  Settings with host `claude.ai`.
- **DCR.** MCP Inspector completes the flow, and the client shows as
  `source = dcr`.
- **Claude Code.** It connects through a loopback redirect on a random
  port. The consent screen shows the loopback warning.
- **Scopes.**
  - A grant without `follows` gets no `add_podcast` in `tools/list`,
    and a direct `tools/call` is refused naming the scope.
  - A non-admin cannot get `pipeline` by asking for it.
  - Demoting an admin removes `pipeline` on the next call.
- **Refresh.**
  - It rotates.
  - Presenting the old refresh token revokes the grant, and the next
    call gets `401`.
  - A suspended user's refresh gets `invalid_grant`.
- **Revocation.** Disconnect in Settings, ban, and logout everywhere
  each stop the next call with `401`.
- **Refusals.**
  - An unregistered `redirect_uri` shows our error page and never
    redirects.
  - `plain` PKCE is refused.
  - A CIMD URL that resolves to a private address is refused.
  - The consent page cannot be framed.
- **The old address still works.** A personal URL minted before the
  change still works.

## Out of scope

- **Lazy authentication** (some tools open without sign-in). It would
  let directory visitors try the catalogue before signing in. It needs
  its own decision on what anonymous MCP may read under the spec #94
  public rules, and on rate limits. Revisit after Phase 3.
- **Enterprise identity assertions** (SEP-990, `identity_assertion_enabled`).
- **Anthropic-held client credentials and credentials entered at
  connection time.**
- **New tools,** such as inbox and briefings (spec #78 says they need
  their own spec).

## Open questions

1. **Refresh lifetime.** Proposed: 90 days *idle*, so active
   connections never lapse. The alternative is a hard cap from consent.
2. **Silent re-approval.** Skip the consent screen when the same client
   asks again for the same or fewer scopes? Proposed: not in Phase 1.
   Revisit if Claude reconnects often enough to annoy readers.
3. **SQLite.** Build both backends per the current repository contract,
   or Postgres only? This depends on spec #107's decision D4 on SQLite
   support.
