# MCP Inbox Import: `import_episode` and `send_to_inbox`

> **Status:** 📋 Planned
> **Created:** 2026-11-04
> **Updated:** 2026-11-04
> **Priority:** Medium. The connector can find episodes but can't act on
> them: "add these five episodes to my inbox" fails today, and the
> operator had to fall back to an SSM script.
> **Related:** [#31 import-arbitrary-episodes](31-import-arbitrary-episodes.md)
> (the import service), [#78 remote-mcp-access](78-remote-mcp-access.md)
> (connector identity and scopes), [#88 import-outcomes](88-import-outcomes-and-arriving-soon.md)
> (outcomes, "Send to my inbox"), [#104 abuse-prevention-and-user-admin](104-abuse-prevention-and-user-admin.md)
> (imports switch, `imports_per_day`), [#109 mcp-oauth](109-mcp-oauth.md)
> (tool annotations).

---

## Overview

The web app has two ways to put one episode in a reader's inbox. **Import**
(`POST /api/imports`) takes an Apple Podcasts, YouTube, Spotify or audio-file
URL, makes the episode if it is new, and delivers it. **Send to my inbox**
(`POST /api/inbox/{episode_id}`) delivers an episode Thestill already has.
The MCP connector has neither. Claude can search the web for an Apple link,
or find an episode with `search_corpus`, but it can't put the episode in the
inbox.

This spec adds the two missing MCP tools, each a thin wrapper over the
service the web route already calls:

- `import_episode(url)` calls `ImportService.import_url`.
- `send_to_inbox(episode_id | episode_url)` calls `InboxService.deliver_to_user`.

Both need the existing `follows` scope. Its label becomes "Follows &
imports", because importing runs the pipeline and costs money, as adding a
feed does. The behaviour matches the web routes exactly: same idempotency,
same outcomes, same pause switch and daily quota. No new tables, no new
scope, no new settings.

## Goals

1. A connector token with `follows` can import an Apple / YouTube / Spotify /
   audio URL into the caller's inbox. Doing it again returns
   `already_in_inbox` and starts no second pipeline.
2. A connector token with `follows` can send an existing episode to the
   caller's inbox by the `episode_id` the citation tools return, or by the
   `episode_url` every tool returns.
3. Every web safeguard applies unchanged: the spec #104 `imports` switch and
   `imports_per_day` quota, read-only mode, and the per-user MCP mutation
   rate limit.
4. A token without `follows` can't see or call either tool. That is the
   fail-closed scope registry from spec #78.
5. stdio keeps working in single-user mode. It delivers to the default user,
   as `add_podcast` does.

## Non-goals

- Finding an episode's URL from its title. Claude has web search, and Apple
  lookups stay in the resolvers.
- Batch import. Claude calls the tool once per URL, and each call counts once
  against the mutation rate limit (clarification 2).
- Removing, hiding or marking inbox rows over MCP. The inbox never loses
  rows on its own (spec #88), and reading-state tools are a separate
  question.
- Tool annotations on all 22 tools. That is spec #109 Phase 2. This spec
  adds two rows to its table.

## Background findings

- `ImportService.import_url(user_id=, url=)` covers the whole web import.
  It checks the switch (`require_feature("imports")`), resolves the URL,
  reserves the quota only when a pipeline would start, runs
  `find_or_create` on the inbox row with `source="import"`, and enqueues the
  pipeline.
  [import_service.py:896](../thestill/services/import_service.py#L896).
  `ImportResult.outcome` is `new_episode` | `added_existing` |
  `already_in_inbox`
  ([import_service.py:834](../thestill/services/import_service.py#L834)).
- Resolver failures raise `UnsupportedUrlError` / `ResolverError`, both
  subclasses of `ImportError`
  ([import_service.py:95](../thestill/services/import_service.py#L95)). The
  web route sends `str(exc)` to the client as a 400
  ([api_imports.py:55](../thestill/web/routes/api_imports.py#L55)). **Not
  every message is written for users** (Codex, PR #465). Some raise sites
  wrap raw exception text: the `requests` failure in `_guarded_get`
  ([import_service.py:362](../thestill/services/import_service.py#L362)), the
  iTunes JSON parse (`:376`), `SpotifyResolutionError` (`:780`) and the
  catch-all in `_resolve`, which wraps any resolver exception (`:1022`).
  Others are authored: the YouTube `describe_youtube_failure` text (`:244`),
  the Apple "not a single episode" hint (`:706`) and `UnsupportedUrlError`
  (`:1023`). So the web route already leaks on the wrapping sites, and
  relaying `ImportError` verbatim over MCP would leak there too, bypassing
  [errors.py](../thestill/mcp/errors.py).
- `ImportService` is wired in the web app with the feed manager (to fetch
  the parent feed once), the transcription provider, the access guard and
  the quota ([app.py:336](../thestill/web/app.py#L336)). The MCP server
  already builds every one of those pieces
  ([tools.py:107](../thestill/mcp/tools.py#L107)).
- `InboxService.deliver_to_user(user_id, episode_id, source="ad_hoc")`
  returns `(entry, created)`. It never changes an existing row. When the
  episode is unprocessed it starts the pipeline, and then the imports switch
  and quota apply
  ([inbox_service.py:355](../thestill/services/inbox_service.py#L355)). The
  web route checks `episode_exists` first so a missing episode is a 404
  ([api_inbox.py:182](../thestill/web/routes/api_inbox.py#L182)).
- Scopes fail closed. A tool missing from `_SCOPE_BY_TOOL` is hidden and
  refused over HTTP, and `tests/unit/mcp/test_scopes.py` checks that every
  registered tool has an entry
  ([scopes.py:14](../thestill/mcp/scopes.py#L14)). `follows` holds
  `add_podcast` and `remove_podcast` today and is off by default
  ([mcp_token.py:31](../thestill/models/mcp_token.py#L31)).
- Mutation guards run before dispatch for any name in `_MUTATING_TOOLS`: the
  read-only panic switch for non-admins, and `enforce_mcp_mutation_quota`
  per user (30/min by default)
  ([tools.py:519](../thestill/mcp/tools.py#L519),
  [rate_limit.py:294](../thestill/web/middleware/rate_limit.py#L294)).
- Errors reach the client only through `public_error_message`. Spec #104
  refusals (`FeaturePausedError`, `QuotaExceededError`) are already
  authored. Anything else becomes a reference id
  ([errors.py:41](../thestill/mcp/errors.py#L41)).
- Tool families live in their own modules, with a `*_tool_definitions()` and
  a `dispatch_*_tool()` that returns `None` for a name it doesn't own
  ([search_tools.py](../thestill/mcp/search_tools.py),
  [entity_tools.py](../thestill/mcp/entity_tools.py)). `tools.py` is 1,823
  lines.
- Citation rows (`search_corpus`, `find_mentions`, `list_quotes_by`) carry
  `episode_id` ([entities.py:192](../thestill/models/entities.py#L192)).
  `list_episodes` carries only `episode_url`, so `send_to_inbox` accepts
  both.
- `get_episode_by_slug(podcast_slug, episode_slug)` resolves an episode page
  URL ([podcast_repository.py:857](../thestill/repositories/podcast_repository.py#L857)).
- On stdio there is no user. `add_podcast` follows for the default user,
  and only in single-user mode
  ([podcast_add.py:66](../thestill/services/podcast_add.py#L66)).
- One scope copy is shared by the Settings card and the OAuth consent page
  ([mcpScopes.ts](../thestill/web/frontend/src/constants/mcpScopes.ts)).

## Clarifications

Answered 2026-11-04.

1. **Which scope?** Reuse `follows`, relabelled "Follows & imports" with an
   updated hint. *Why:* importing costs pipeline money just like adding a
   feed, and a new scope would touch `ALL_SCOPES`, the token and OAuth
   services, the consent page and their tests for a split no reader has
   asked for. Existing `follows` tokens get the tools on their next
   `tools/list`. A `read`-only token, like the operator's today, has to
   change scopes in Settings.
2. **One URL or a list?** One URL per call (`import_episode(url)`). *Why:*
   it matches `POST /api/imports`, keeps each result simple, and each call
   counts once against the mutation limit. Claude calls it N times.
3. **Also send an existing episode by id?** Yes, `send_to_inbox`. *Why:* the
   connector's most natural path is search → "put that in my inbox", and the
   web already has this exact action ("Send to my inbox").

Decided while drafting, not asked:

- **stdio:** single-user mode delivers to the default user (the
  `add_podcast` parity). Multi-user stdio has no caller, so both tools
  refuse with an authored message pointing at the connector.
- **`send_to_inbox` input:** exactly one of `episode_id` (UUID) or
  `episode_url` (a `/podcasts/<slug>/episodes/<slug>` page on this
  instance; the host isn't checked, only the path). Both or neither is an
  error.
- **Response links:** both tools return `episode_url`, built the same way as
  every other tool, so Claude can link the episode page.
- **Import error text (Codex P2 on PR #465):** `ImportError` carries two
  texts. `str(exc)` is the detail and goes only to the log.
  `exc.public_message` is what a client may see. A raise site sets it with
  `public=` only when the text was written for a user (the YouTube failure
  text, the Apple "not a single episode" hint, Apple "no such episode", and
  `UnsupportedUrlError`). Otherwise it falls back to "Couldn't read that
  link. Check it's a public episode link and try again." The MCP tool *and*
  the web route return only `public_message` and log the detail with
  `exc_info`, so this fixes the web leak too, in the shared service rather
  than in two callers. A message built only from the caller's own URL and
  fixed wording counts as authored. One with exception text or upstream
  bodies in it never does.

## Execution plan

### Phase A: tools, wiring, scope

| # | File | Change |
|---|------|--------|
| A0 | `thestill/services/import_service.py`, `thestill/web/routes/api_imports.py` | `ImportError.__init__(detail, *, public=None)` and a `public_message` property (generic fallback; `UnsupportedUrlError` is authored by default). Pass `public=` at the authored raise sites only. The web route returns `public_message` and logs the detail. Tests: each wrapping site (`:362`, `:376`, `:780`, `:1022`) yields the generic text with no exception text in it; the authored sites keep their wording; the web 400 body for a `requests` failure has no exception text |
| A1 | `thestill/mcp/inbox_tools.py` (new) | `INBOX_TOOL_NAMES`, `inbox_tool_definitions()`, `dispatch_inbox_tool(name, arguments, *, user_id, import_service, inbox_service, podcast_repository, base_url)`. `import_episode` maps `ImportResult` to `{success, outcome, message, episode: {id, title, podcast_title, state, failed, episode_url}}`, and an `ImportError` becomes `{success: false, error: exc.public_message}`, with the detail logged. `send_to_inbox` resolves `episode_id` or `episode_url`, gives a clean "not found", then `deliver_to_user(source="ad_hoc")` → `{success, created, message, episode}` |
| A2 | `thestill/mcp/tools.py` | Build `ImportService` with the web wiring. Add both names to `_MUTATING_TOOLS`. List the definitions. Dispatch after the existing branches with the caller's user id: remote is `identity.user.id`; stdio single-user is the default user; stdio multi-user is refused |
| A3 | `thestill/mcp/scopes.py` | `import_episode`, `send_to_inbox` → `SCOPE_FOLLOWS` |
| A4 | `tests/unit/mcp/test_inbox_tools.py` (new) | Unit tests on the dispatcher with fakes: each outcome, `ImportError` text, the `episode_url` parse, not-found, both-or-neither args. End to end through the HTTP harness: a `read` token neither lists nor calls the tools; a `follows` token imports (resolver stubbed) and a repeat import returns `already_in_inbox` with one inbox row and one pipeline task; `send_to_inbox` creates once then returns `created: false`; read-only mode refuses a non-admin |
| A5 | `tests/unit/mcp/test_scopes.py` | Assert both tools sit in `follows` |

**Gate A:** `./venv/bin/python -m pytest tests/unit/mcp tests/unit/web/test_mcp_http.py tests/unit/services/test_import_service*.py tests/integration/web/test_api_imports.py -q`
is green, and `ruff check` passes on the changed files.

### Phase B: copy and docs

| # | File | Change |
|---|------|--------|
| B1 | `thestill/web/frontend/src/constants/mcpScopes.ts` | `follows`: label "Follows & imports", hint "Add podcasts, unfollow, and import episodes into your inbox. Both run the pipeline, so this is off by default." |
| B2 | `McpConnectorSettings.test.tsx`, `OAuthConsent.test.tsx` | Follow the new label and hint |
| B3 | `docs/mcp-usage.md` | "What a connector URL can do" bullets; two entries under Available Tools |
| B4 | `specs/78-remote-mcp-access.md`, `specs/109-mcp-oauth.md` | Scope table row for `follows`; annotation table rows (`readOnlyHint: false`, `destructiveHint: false`) |

**Gate B:** `npx vitest run src/components/McpConnectorSettings.test.tsx src/pages/OAuthConsent.test.tsx`
is green (run from `thestill/web/frontend`), and markdownlint is clean on
the changed docs.

### Phase C: after merge (operator)

1. Tag and deploy (the `v*` tag runs `deploy.yml`).
2. In Settings, change the connector's scopes to include "Follows &
   imports".
3. From Claude: import the five Eric Schmidt episodes (Pattern Breakers, No
   Stupid Questions, Prof G, Diary of a CEO, Clear+Vivid). Check that the
   outcomes are sensible and that a second run says `already_in_inbox` for
   all five.

## Alternatives considered

- **A new `inbox` scope.** Finer-grained: import without being able to add
  or unfollow feeds. Rejected in clarification 1. It is plumbing across the
  token service, the OAuth consent and the UI for a split nobody needs yet.
  It can still be carved out later without breaking tokens, because
  `follows` would keep both tools.
- **Batch `import_episodes(urls[])`.** Fewer round trips. Rejected in
  clarification 2: partial failures and quota accounting get muddier, and
  it would weaken the rate limit's per-call meaning.
- **Inline branches in `tools.py`.** Smallest diff. Rejected: the file is
  already 1,823 lines, and search and entity tools set the precedent of a
  module per tool family with its own unit-testable dispatcher.
- **The MCP server calling the web API over HTTP.** It would reuse the route
  literally. Rejected: the MCP mount runs in the same process with the same
  services, and an HTTP hop would need a session for the token's user.

## Files touched

**Created:** `thestill/mcp/inbox_tools.py`, `tests/unit/mcp/test_inbox_tools.py`

**Modified:** `thestill/services/import_service.py`,
`thestill/web/routes/api_imports.py`, `thestill/mcp/tools.py`,
`thestill/mcp/scopes.py`,
`tests/unit/mcp/test_scopes.py`,
`thestill/web/frontend/src/constants/mcpScopes.ts`,
`thestill/web/frontend/src/components/McpConnectorSettings.test.tsx`,
`thestill/web/frontend/src/pages/OAuthConsent.test.tsx`,
`docs/mcp-usage.md`, `specs/78-remote-mcp-access.md`,
`specs/109-mcp-oauth.md`, `specs/README.md`

**Deleted:** none

## Open questions

- **The synthetic `audio-imports` parent.** For a bare audio URL the
  podcast slug may be empty in `ImportResult`, and the episode link may not
  build. Resolved in Phase A by checking `episode_links` with an empty slug.
  If it fails, `episode_url` is `null`, not a broken link.
- **Usage events.** Spec #95's `mcp_tool_call` already counts every call,
  and spec #111's `user_connector_days` counts successful ones, so nothing
  new is planned. Revisit if the Scorecard wants imports split by surface.

## Rollback plan

Revert the PR. No migrations, no data rewrites, no API changes: the tools
disappear from `tools/list` on the next deploy. Inbox rows and episodes
created through them stay, exactly as web imports would. The scope label
change is copy only.
