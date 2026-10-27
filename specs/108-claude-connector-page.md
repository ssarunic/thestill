# Claude Connector: a Public Page, Entry Points and Measurement

> **Status:** 📝 Draft (2026-10-27)
> **Created:** 2026-10-27
> **Updated:** 2026-10-27
> **Priority:** Medium. The connector has worked for every user since v1.4.1 (2026-09-08), but nobody outside the operator has used it, and today we could not tell if someone tried and gave up.
> **Author:** Product & Engineering
> **Related:** [#78 remote-mcp-access](78-remote-mcp-access.md) (per-user connector URLs, scopes, Phase 3 OAuth), [#94 public-pages](94-public-pages.md) (public routes, head tags, sitemap), [#95 product-analytics](95-product-analytics.md) (Layer B events and their privacy rules), [#105 server-rendered-public-pages](105-server-rendered-public-pages.md) (server-written fallback HTML for crawlers).

---

## Executive summary

Any signed-in reader can connect thestill to Claude and ask about
their podcasts from claude.ai, the desktop app or a phone. Hardly
anyone can find out that this exists:

- The landing page has half a sentence on it, with no link: "Search
  every mention of anything across every show you follow, and ask
  Claude about it."
- The only other place it appears is the "Claude connector (MCP)" card
  on Settings.

This spec does three things:

1. **Measure first (Phase 1).** Record when a reader creates a
   connector, where they came from, and which client calls the tools.
   This must be live before any promotion, so we know whether the
   promotion works.
2. **A public page at `/claude` (Phase 1).** It says what the
   connector does, gives example prompts, explains setup in three
   steps, and states what Claude can and cannot see. The landing page
   links to it, the server renders it for crawlers, and it is in the
   sitemap.
3. **Entry points in the app (Phase 2).** Offer the connector where a
   reader is already looking for an answer: the episode reader, search
   results and entity pages. Also show a one-time card once a reader
   follows three shows.

Getting into connector directories needs OAuth (spec #78 Phase 3).
This spec lists the directories but does not build OAuth.

## Problem and evidence

PostHog, 2026-10-17 to 2026-10-27 (all of `mcp_tool_call` since
PostHog went live):

- **56 tool calls, all from one account, the operator's admin
  account.** They break down as `get_entity` 19, `list_episodes` 17,
  `get_summary` 9, `list_podcasts` 6, `get_transcript` 3 and
  `get_status` 2. Readers other than the operator made zero calls.
- **About 40 signed-out visitors a day.** That is the light-id count
  after the v1.30.0 prerender removed crawler traffic. Google sends a
  growing share of them to episode pages. None of these visitors sees
  anything about Claude.
- **Two to five signed-in people a day,** the operator included.

Three gaps follow from this:

1. **Discovery.** The feature is invisible to visitors and nearly
   invisible to signed-in readers.
2. **Explanation.** Even a reader who finds the Settings card learns
   *how* to set it up, not *why* they would. The card has no example
   of a question worth asking.
3. **Measurement.** Minting a connector sends no event, so creating one
   and never using it looks the same as never trying. `mcp_tool_call`
   has no client field, and PostHog's own `$virt_mcp_harness` is empty
   on our server-sent events. We cannot tell Claude from any other
   client.

## Design

### 1. Events (Phase 1)

All new events follow spec #95's rules:

- Each event is a model in `services/analytics_sink.py` with
  `extra="forbid"`.
- No podcast, episode, entity or prompt text is sent.
- The per-user opt-out and Global Privacy Control drop the event.

| Event | Sent by | Props |
|---|---|---|
| `mcp_connector_created` | server, `POST /api/me/mcp-token` | `scopes`: sorted list from `read`, `follows`, `pipeline`. `rotated`: bool. `source`: `ConnectorSource` (below) |
| `mcp_connector_revoked` | server, `DELETE /api/me/mcp-token` | none |
| `mcp_tool_call` (changed) | server, remote MCP | adds `client`: `McpClient` (below) |
| `public_page_view` (changed) | server beacon | `kind` gains `claude` |

**`source`** says where the reader came from. It follows the
`FollowSource` pattern:

- `ConnectorSource = Literal["claude_page", "episode", "search",
  "entity", "nudge", "settings", "other"]`.
- The client sends `source` in the POST body, and
  `connector_source()` maps any unknown value to `other`.
- Entry points pass it to Settings in router state, never in the URL,
  so a shared link carries no attribution.
- Opening Settings directly gives `settings`.

**`client`** is a coarse bucket, never the raw string:

- `McpClient = Literal["claude", "chatgpt", "cursor", "vscode",
  "other", "unknown"]`.
- The connector runs stateless (spec #78), so the `initialize`
  handshake's `clientInfo` does not reach later `tools/call` requests.
  The guard in `web/mcp_http.py` already sees every request, so it
  buckets the HTTP `User-Agent` there.
- It stores the bucket on the ASGI scope state next to `STATE_GPC`,
  and `_count_tool_call` reads it from there.
- No header gives `unknown`. A header that matches no pattern gives
  `other`.
- The patterns live in one table with a unit test per bucket.
- **Phase 0 of this event:** before writing the table, log the raw
  `User-Agent` of MCP requests to structlog only, at debug level, for
  one week. The patterns must come from real headers, not guesses.

**`claude` page kind:**

- `public_page_kind("/claude")` returns `claude` in `utils/page_kind.py`.
- Add `claude` to `PublicKind` and `PageKindOrNone` in
  `analytics_sink.py`.
- Add `claude` to `SIGNUP_SOURCES`, so a sign-in that started on the
  page shows in the spec #95 funnel. Check first that
  `users.signup_source` has no check constraint that would refuse the
  new value.

**The measure:**

- The success number is **people other than admins with at least one
  `mcp_tool_call` in a week.** Baseline on 2026-10-27: 0.
- Exclude admins with the `is_admin` person property. The test-account
  cohort does not exclude the owner.
- The funnel is `public_page_view{kind=claude}` → `login_completed` →
  `mcp_connector_created` → `mcp_tool_call`. Add both, the funnel and
  the weekly number, to the "thestill: product" dashboard in
  `docs/analytics.md`.

### 2. The `/claude` page (Phase 1)

**Route.** `/claude`, readable signed in or signed out, like `/terms`
and `/privacy`.

- `App.tsx` gets a route next to the legal pages.
- `public_shell._static("claude")` writes head tags:
  - title "Use thestill in Claude · thestill"
  - a description
  - canonical `/claude`
  - `index=True`
- It goes in the sitemap's fixed URLs next to `/` and `/top`.
- It goes in `PRERENDER_KINDS` with a static fallback in
  `public_prerender.py`. The page has no data, so the fallback is the
  same text the React page renders, from one shared source. That keeps
  the spec #105 parity rule.

**Name.** Use `/claude`, not `/connect` or `/ai`. Today only Claude
can use the connector, because the token URL needs a client that
accepts a bare URL (spec #78 Phase 2). If OAuth lands and other
clients work, add the general page then and redirect `/claude` to it.

**Content, top to bottom:**

1. **Heading and one sentence.** "Ask Claude about your podcasts."
   Then: thestill connects to Claude on the web, desktop and phone, so
   Claude can read the summaries, transcripts and quotes of the shows
   you follow, and the full thestill catalogue for search.
2. **What you can ask.** Five prompts, each with a one-line note on
   what Claude does with it. The notes must match a real tool on the
   `read` scope, so the page never promises something the connector
   cannot do:
   - "What came out this week on the shows I follow? Pick the three
     worth reading." This is `list_podcasts` (your follows),
     `list_episodes` and `get_summary`.
   - "Who has talked about Nvidia this month, and what did they say?"
     This is `find_mentions`, `get_entity` and `list_quotes_by`.
   - "What has [a guest] said about housing? Quote them with
     timestamps." This is `list_quotes_by` and `search_corpus`.
   - "Give me the moment they argued about rate cuts, so I can listen
     to it." This is `search_corpus` and `get_episode_clip`, which
     returns a link that plays from that second.
   - "Summarise the latest [show] in five bullets, then tell me what
     the guest got wrong." This is `get_summary` and `get_transcript`.

   The operator picks the final prompts from real sessions and
   replaces the brackets with real names that are on thestill.
3. **One real exchange.** A screenshot or short recording of one prompt
   and Claude's answer, with timed quotes visible. Take it from a real
   session against prod. Never mock it up. Store it as a static asset
   with alt text that carries the prompt and the gist of the answer,
   for crawlers and screen readers.
4. **Set it up in three steps:**
   1. Sign in to thestill and open Settings → Claude connector.
   2. Choose what Claude may do. Read is always on. "Follow and
      unfollow shows" is off unless you turn it on. Then copy your
      connector URL.
   3. In Claude, open Settings → Connectors → "Add custom connector",
      paste the URL, and save. It also works in Claude's phone and
      desktop apps.

   The button is "Create your connector":
   - Signed in, it goes to `/settings#claude-connector`.
   - Signed out, it goes through `/login` with `intent=connector`, per
     the button-label rule in spec #102: the label names the action,
     and sign-in happens on the click.
5. **What Claude can and can't see.** Plain statements, each true
   today:
   - Claude sees what you can see in thestill, and nothing else.
   - It cannot follow or unfollow shows unless you allow it.
   - The URL is a password: anyone who has it can read as you. Keep
     it private.
   - Revoke it any time from Settings. Revoking stops it at once.
   - It expires after 90 days (the default). Rotate it to get a new one.
   - Unless you turn off usage events, thestill records which tool was
     called and whether it worked. It never records your question or
     Claude's answer.
6. **Questions.**
   - Which Claude plans support custom connectors? Link to Anthropic's
     help page instead of restating plan rules, which change.
   - Does it work with ChatGPT? Not yet.
   - Does it cost anything? Not on the thestill side.

**Landing link.** The "Your knowledge base" outcome on the landing page
ends with a link to `/claude`: "…and ask Claude about it. See how."

**Settings anchor.** Give the connector card `id="claude-connector"` so
the entry points can scroll to it. Read `source` from router state and
send it with the create request.

### 3. Entry points in the app (Phase 2)

Each entry point is one quiet line or menu item, never a modal. It has
two forms, chosen from `GET /api/me/mcp-token`'s `state`, which the
app already fetches for Settings:

| Reader has an active connector | Shown |
|---|---|
| No | "Ask Claude about this" → `/settings#claude-connector` with `source` |
| Yes | "Ask Claude about this" → opens Claude with the question pre-filled |

**Pre-fill.** Use `https://claude.ai/new?q=<encoded prompt>`. The
prompt names the episode, entity or search by title and by its public
thestill URL, so Claude's tools can resolve it.

- Check that the `q` parameter still pre-fills before building this.
- If it no longer does, the "Yes" form copies the prompt to the
  clipboard and opens `https://claude.ai/new`.

**Places:**

- **Episode reader:** an item in the ⋯ More menu, `source=episode`.
  Prompt: "Using thestill, summarise <episode title> (<url>) and list
  the strongest claims with timestamps."
- **Search results:** a line under the results, `source=search`.
  Prompt: "Using thestill, what have podcasts said about <query>?"
  The query is already on the reader's screen. It goes into the
  Claude URL the reader opens, never into an analytics event.
- **Entity page:** a line under the header, `source=entity`. Prompt:
  "Using thestill, what has been said about <name> (<url>)?"
- **Nudge:** one card on the inbox, `source=nudge`.
  - Shown once a reader has three or more follows and no connector.
  - It sits above the list and removes no inbox rows (the
    inbox-never-removes-rows rule).
  - Dismissing it is permanent for that reader. Store the dismissal
    with the user's other UI preferences if such a store exists by
    then. Otherwise use `localStorage` (try/catch), and accept that it
    may show once per device.
  - On a phone it goes under the tabs, like the spec #90 follow prompt,
    so it does not push the reader's first row below the fold.

### 4. Directories (no code; follow-up)

These are where people look for MCP connectors. Each one lists a server
under a fixed URL, so the per-user token URL does not fit any of them
until OAuth exists (spec #78 Phase 3). Recorded so the OAuth work has
its payoff written down:

- Anthropic's connector directory in Claude. This is the one that
  matters most. Check its current requirements when OAuth is planned.
- The official MCP Registry (a `server.json` and namespace
  verification).
- Smithery, PulseMCP, mcp.so and the awesome-mcp-servers list on
  GitHub.

Before OAuth, the honest listing is a link to `/claude` with "get your
personal URL from Settings". Do that only where a directory allows it.

## Phases

### Phase 1: measure and explain

- [ ] Log the raw MCP `User-Agent` at debug level for one week, then
  write the `McpClient` patterns from what was seen.
- [ ] `mcp_connector_created` (`scopes`, `rotated`, `source`) and
  `mcp_connector_revoked`.
- [ ] `client` on `mcp_tool_call`.
- [ ] `claude` page kind: page_kind, analytics kinds, signup source,
  head tags, sitemap, prerender fallback.
- [ ] `/claude` page with the five sections and the real exchange.
- [ ] Landing "See how" link. Settings card anchor and `source`
  passthrough.
- [ ] `docs/analytics.md`: event table, dashboard insights.
- [ ] Privacy policy: name connector creation alongside "connector
  calls" if the current wording does not already cover it.

### Phase 2: entry points

- [ ] Shared `AskClaude` component with the two forms. Check the
  pre-fill and use the fallback if it fails.
- [ ] Episode ⋯ menu, search results line, entity page line.
- [ ] Inbox nudge card with permanent dismissal and the phone layout.

### Phase 3 (follow-up spec): OAuth and directories

Owned by spec #78 Phase 3. Not started here.

## Acceptance

Phase 1:

- Creating a connector from Settings sends one
  `mcp_connector_created` with `source=settings`. Coming from `/claude`
  sends `source=claude_page`. Rotating sends `rotated=true`.
- An opted-out or GPC reader creates a connector and no event leaves
  the server (unit test on the sink, as for `follow`).
- A tool call from claude.ai arrives in PostHog with `client=claude`.
  The `User-Agent` bucketing has a unit test per bucket, and one for a
  missing header.
- `curl -A "Mozilla/5.0 (compatible; bingbot/2.0)" https://thestill.me/claude`
  returns the page text in the body, and the text matches what React
  renders (parity test).
- `/claude` is in `sitemap.xml`. Its head has the canonical URL and
  index, follow.
- The landing link and the page work signed out on a 375 px phone with
  no horizontal scroll.

Phase 2:

- With no connector, each entry point opens Settings at the card and
  carries its `source`. With an active connector, it opens Claude with
  the prompt pre-filled.
- The nudge shows once at three follows, never again after dismissal
  on that device (or account), and never removes an inbox row.

## Out of scope

- OAuth, organisation connectors and directory listings (spec #78
  Phase 3).
- New MCP tools such as inbox or briefing tools (spec #78 says these
  need their own spec).
- Email or social announcements. That is operator work after Phase 1
  ships, so the funnel can measure it.
- Recording prompts, answers or tool arguments, now or later.

## Open questions

1. **Should one `/claude` page cover ChatGPT later, or should it get
   its own?** Proposed: one general page once OAuth works, with
   `/claude` redirecting to it.
2. **Should the "Yes" form of an entry point show at all on desktop
   when Claude Desktop is installed?** It opens claude.ai in the
   browser, not the app. That is acceptable for now, because the
   connector is synced to the reader's Claude account, not to one app.
