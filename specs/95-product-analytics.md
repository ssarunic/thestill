# Product Analytics

> **Status:** 🚧 Phases 0–2 implemented (2026-10-16); PostHog project setup and `POSTHOG_PROJECT_KEY` pending
> **Created:** 2026-10-08
> **Updated:** 2026-10-16 (v2: PostHog replaces the first-party event log; Phases 0–2 built)
> **Priority:** Medium, but Phases 0 and 1 must land before any launch driven by spec #94: a launch nobody can measure teaches nothing.
> **Author:** Product & Engineering
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP `connect-src 'self'`), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (read state), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#50 scheduled-briefings](50-scheduled-briefings.md) / [#84 scheduled-only-briefings](84-scheduled-only-briefings.md), [#66 aws-single-ec2-hosting](66-aws-single-ec2-hosting.md) (Caddy in front), [#75 llm-call-tracing](75-llm-call-tracing.md) (rejected PostHog for traces), [#78 remote-mcp-access](78-remote-mcp-access.md), [#80 share-to-thestill](80-share-to-thestill.md) (`next` through login), [#86 quality-feedback-loop](86-quality-feedback-loop.md), [#91 transcript-summary-export](91-transcript-summary-export.md), [#94 public-pages](94-public-pages.md), [#102 sign-in-page](102-sign-in-page.md)

---

## Executive summary

Thestill has no product analytics. Nobody can answer how many people
signed up last week, how many of them followed a show, how many opened
their first briefing, or how many came back.

Most of the answers already exist as first-party state. A signup is a
`users` row, a follow is a `podcast_followers` row, a briefing is a
`briefings` row with `listened_at`, a read episode is an inbox entry
with `state_changed_at`. The product's own tables are the most accurate
funnel there is, and they need no collection at all.

So the design has two layers:

| Layer | Source | Answers | Where you read it |
|---|---|---|---|
| A. Derived funnel | Existing tables | Signups, activation steps, cohort retention, briefing open rate | `/analytics` admin page, `thestill analytics` |
| B. Usage events | PostHog Cloud (EU), fed by a short allowlist of explicit events from the signed-in app and from the server | Public page views and referrers, sign-in starts, feature use, MCP tool calls, WAU/MAU, ad-hoc exploration | PostHog |

v1 of this spec built Layer B in-house: a `product_events` table, a
`POST /api/events` endpoint, nightly rollups, a retention job and
hand-built charts. v2 hands that layer to PostHog. That gives
exploration, funnels and dashboards for free, and saves building an
event store that would only ever answer the questions listed in advance.

PostHog is set up so that the privacy guarantees of v1 mostly hold:

- **CSP unchanged.** The SDK is bundled from npm, so no external script
  loads. Events go to `/ingest` on our own domain, which Caddy forwards
  to PostHog's EU host.
- **No cookie and no browser storage.** The SDK keeps its state in
  memory only, so there is nothing to ask consent for and no banner.
- **No SDK on public pages or the sign-in page.** Those are counted
  server-side, so anonymous visitors run no third-party code at all.
- **No autocapture, no session replay, no surveys, no flags.** Only
  allowlisted events are sent, with allowlisted props.
- **Only the internal user UUID** identifies a person. No email, name,
  query text, transcript text or raw URL is sent.
- **Opt-outs are honoured server-side.** Global Privacy Control and a
  per-user Settings switch both apply.
- **Off when there's no key**, which is the default for single-user
  installs and local development.

The rule from v1 still holds: a fact already in a table is read from the
table and never also sent as an event.

## Outcomes

| # | Outcome | Verified by |
|---|---|---|
| O1 | An admin can read, for any 7/28/90-day window: signups, % who followed ≥1 show within 7 days, % whose first briefing was opened, weekly cohort retention | Postgres contract tests on the funnel queries, `@live` check after deploy |
| O2 | PostHog shows public page views by kind and referrer, and sign-in starts and completions by page kind. The admin page shows signups and activation by the public page they came through. | pytest on the server emitters with a fake sink; Postgres contract test on `signup_source` |
| O3 | Feature use is countable in PostHog: search, export, share, narration play, entity page opens, remote MCP tool calls | pytest per server emitter, Vitest per client emitter |
| O4 | Nothing identifies a person beyond the internal user id. No cookie or browser storage is written. CSP is unchanged. The browser never contacts a PostHog domain. | header test, Playwright storage and network assertions |
| O5 | GPC and the per-user Settings switch stop events on both the client and the server | pytest + Vitest |
| O6 | Without a PostHog key, and always in single-user mode, nothing is sent and no SDK code is loaded | pytest + Vitest |

## Current state

- **Frontend.** No analytics, tracker, beacon or pixel anywhere under
  `thestill/web/frontend/src`. The CSP at
  [security_headers.py:51-63](../thestill/web/middleware/security_headers.py#L51-L63)
  pins `connect-src 'self'` and `script-src 'self'` plus YouTube.
- **Edge.** Production runs behind Caddy
  ([deploy/aws-ec2/Caddyfile](../deploy/aws-ec2/Caddyfile)), which
  reverse-proxies everything to `thestill:8000`. Adding a second
  `handle_path` for `/ingest` is a few lines.
- **Runtime config.** The frontend reads capability flags from
  `GET /api/auth/status`
  ([auth.py:154](../thestill/web/routes/auth.py#L154)), which already
  carries `multi_user`, `public_pages` and `email_delivery_available`.
  The PostHog key goes there too, so one image serves every
  environment without a build-time variable.
- **Server.** [logging_middleware.py](../thestill/web/middleware/logging_middleware.py)
  logs every request to CloudWatch. That is operational data, not
  product data. Spec #94 shipped (aa8b5fa), but the `public_page.view`
  log line from its §7 was never implemented.
- **Login `next`.** `/api/auth/google/login` stores a sanitised `next`
  path in the `oauth_next` cookie and the callback reads it back
  ([auth.py:199-320](../thestill/web/routes/auth.py#L199-L320)). Both
  ends are where the sign-in events go.
- **State with timestamps already present.** `users.created_at`,
  `users.last_login_at`; `podcast_followers.created_at`;
  `user_episode_inbox.delivered_at` and `state_changed_at` with
  `state ∈ {unread, read, saved, dismissed}`; `briefings.created_at`,
  `listened_at`, `episode_count`; `mcp_tokens.created_at`,
  `last_used_at`.
- **Privacy policy.**
  [privacy.md](../thestill/web/frontend/public/legal/privacy.md)
  (PR #354, filled in by #355) says "public pages set no cookies and run
  no third-party trackers" and "we use no advertising or analytics
  cookies". It lists AWS and the Gemini API as processors. Phase 1 must
  change it in the same PR that turns PostHog on (§5).
- **No account deletion** exists yet. When it does, it must also delete
  the person in PostHog (§5).
- **Admin surface.** [api_dashboard.py](../thestill/web/routes/api_dashboard.py)
  serves `/stats`, `/activity`, `/narration` behind `require_admin`.
  Admin nav items live in
  [navigation.tsx](../thestill/web/frontend/src/constants/navigation.tsx)
  and render in both the sidebar and the mobile drawer.

## Questions the system must answer

Each question names its layer and source so the implementation cannot
drift into collecting things it does not need.

| Area | Question | Layer | Source |
|---|---|---|---|
| Acquisition | Public page views per day by kind (landing, episode, podcast, entity, top), excluding bots | B | server `public_page_view` |
| Acquisition | Top referrer hosts for public views | B | same event, `referrer_host` |
| Acquisition | Sign-in starts from public pages, which page kind, and how many complete | B | server `login_started` (`from_kind`) → `login_completed` (`next_kind`) |
| Acquisition | Signups per day, and how many arrived through each kind of public page | A | `users.created_at`, `users.signup_source` (§2) |
| Activation | Every activation step below, broken down by signup source | A | `users.signup_source` |
| Activation | % of new users who followed ≥1 podcast within 24 h and 7 d, and median time to first follow | A | `podcast_followers` |
| Activation | % whose first briefing was generated, and % who opened it | A | `user_briefings.created_at`, `user_briefings.opened_at` (§2) |
| Activation | % who read ≥3 episodes in their first week | A | `user_episode_inbox.state = read`, `state_changed_at` |
| Engagement | Weekly and monthly active users | B | PostHog, any identified event in the window |
| Engagement | Briefings opened ÷ briefings generated, per week | A | `briefings.created_at`, `opened_at` |
| Engagement | Episodes read per active user per week | A | inbox read transitions |
| Engagement | Searches, exports, shares, narration plays, entity page opens per week | B | events below |
| Engagement | Remote MCP tool calls per user per week, by tool | B | server `mcp_tool_call` |
| Retention | For each signup week: % active in weeks 1, 2, 4, 8 | A | cohort = `users.created_at`; active = a read transition, a follow or a briefing opened |
| Quality | Spec #86 reports per 1,000 episode reads | A | #86 report table ÷ read transitions |

Anything not in this table is not collected. Adding a row means editing
this spec first (constitution §10). PostHog makes new questions easy to
ask of the events that already exist; it does not make new events
cheaper to add.

Retention moves fully to Layer A in v2. It can't depend on events,
because opted-out users and blocked requests would make cohorts look
worse than they are.

## Design

### 1. Rule: state first, events second

If a fact already lives in a table with a timestamp, it is read from
that table and **never** sent as an event as well. Two records of the
same fact drift (FM-6) and the table is the one the product trusts.
Events exist only for things that leave no durable state: a view, a
click, a search, an export, a tool call.

This means PostHog alone cannot draw the activation funnel. That is
deliberate: the activation funnel is Layer A, and follows and briefings
are not mirrored into PostHog (decision D3). When a question needs a
fact that no table holds, the fix is to store that fact once, as the two
new columns below do, not to send an event.

### 2. Layer A: the derived funnel

A new `AnalyticsRepository` with a Postgres implementation and a
single-user stub that reports "unavailable". It exposes four read
methods, each one SQL statement over existing tables, parameterised by
a window:

- `signups(window)`: daily counts, split by `signup_source`.
- `activation(window)`: for users created in the window, the follow /
  briefing-generated / briefing-opened / three-reads steps with counts,
  percentages and median hours to each step, overall and per
  `signup_source`.
- `cohorts(weeks)`: signup-week rows × activity-week columns.
- `engagement(window)`: reads per active user, briefings opened ÷
  generated, follows per user.

Results are plain Pydantic models (constitution §5). Queries are indexed
by columns that already exist; the one addition is an index on
`user_episode_inbox (state, state_changed_at)` if the planner wants it, decided
by `EXPLAIN` on a production-sized copy, not guessed.

**Two new columns**, one Alembic migration, Postgres and SQLite:

- **`users.signup_source`** (text, null). Set once, in the OAuth
  callback, when the user row is created: the public page kind parsed
  from the `oauth_next` cookie (`episode`, `podcast`, `entity`, `top`,
  `landing`), `share` for a spec #80 share target, or `direct`. Never
  updated afterwards. Existing users stay `null` and are reported as
  "before tracking". It answers "which pages bring users who stay"
  without any vendor, ad blocker or opt-out in the way.
- **`user_briefings.opened_at`** (timestamptz, null). The first time
  the owner opened the briefing. `listened_at` can't serve this, because
  it's only set by the manual "Mark listened" button on
  [BriefingDetail.tsx](../thestill/web/frontend/src/pages/BriefingDetail.tsx).
  It's set by a new `POST /api/briefings/{id}/opened`, idempotent
  (`opened_at = COALESCE(opened_at, now)`), owner-only. The briefing page
  calls it once the briefing has loaded. The web app has no narration
  audio player yet (narrations are text on the same page), so a
  first-play hook waits for briefing audio to ship. "Mark listened" also
  fills `opened_at` when it is still empty. It's a POST
  rather than a side effect of the GET so React Query prefetches and
  refetches can't mark a briefing opened. The migration backfills
  `opened_at = listened_at` where that is set; older briefings opened
  without the button stay `null`, and the admin page says opens before
  the migration date are undercounted.

### 3. Layer B: PostHog

#### 3.1 Project and settings

One PostHog Cloud project in the **EU region**, used only by production.
Development and staging have no key and send nothing. Settings in the
PostHog project, recorded as a checklist in `docs/analytics.md` because
they live outside the repo:

- **Discard client IP data:** on.
- **Autocapture, heatmaps, web vitals, dead clicks, exception capture,
  session replay, surveys:** all off at project level as well as in the
  SDK, so an SDK upgrade that changes a default still sends nothing.
- **Person profiles:** identified users only.
- **Filter out internal and test users:** on, matching the person
  property `is_admin = true`, and applied by default to every insight.
  Admin events are still sent, so production can be checked end to
  end, but no chart counts them unless someone turns the filter off on
  purpose (decision D5).
- **Billing limit** set at the free tier, so a bug can't run up a bill.
- **Organisation members:** the operator only. PostHog shows each
  person's event stream by UUID, which is a per-user view thestill
  itself deliberately does not have.
- **DPA** accepted in the organisation settings before Phase 1 ships,
  after checking that it covers transfers under UK GDPR.

#### 3.2 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `POSTHOG_PROJECT_KEY` | empty | The `phc_…` project key. Public by design; it can only send events. Empty means analytics is off. |
| `POSTHOG_HOST` | `https://eu.i.posthog.com` | Where the server SDK sends. The browser always uses `/ingest`. |
| `ANALYTICS_ENABLED` | `multi_user` | Forced off in single-user mode, whatever the key says. |

`/api/auth/status` gains `analytics: {"key": "phc_…"} | null`. It is
`null` when analytics is off, or when the signed-in user has opted out.
The frontend loads no PostHog code when it is `null`.

#### 3.3 Ingestion through our own domain

Caddy forwards `/ingest` to PostHog. This is the setup PostHog
documents for Caddy:

```caddy
handle_path /ingest/static/* {
    rewrite * /static{path}
    reverse_proxy https://eu-assets.i.posthog.com:443 {
        header_up Host {upstream_hostport}
    }
}
handle_path /ingest/* {
    reverse_proxy https://eu.i.posthog.com:443 {
        header_up Host {upstream_hostport}
    }
}
```

This keeps `connect-src 'self'` and `script-src 'self'` byte-identical,
and it keeps content blockers from dropping events, which would make
PostHog numbers lower than reality by an unknown amount. Locally there
is no Caddy and no key, so nothing is sent.

#### 3.4 Browser SDK

`posthog-js` from npm, loaded with a dynamic `import()` only inside the
signed-in app shell, after `/api/auth/status` returns a key. It is never
loaded on public pages (#94) or the sign-in page (#102), so their bundle
and first paint carry none of it.

Required behaviour (exact option names get pinned against the installed
SDK version when it's implemented):

```ts
posthog.init(key, {
  api_host: "/ingest",
  ui_host: "https://eu.posthog.com",
  persistence: "memory",          // no cookie, no localStorage
  person_profiles: "identified_only",
  autocapture: false,
  capture_pageview: false,        // sent by the router, sanitised
  capture_pageleave: false,
  disable_session_recording: true,
  disable_surveys: true,
  capture_heatmaps: false,
  capture_dead_clicks: false,
  // feature flags and remote config off; see non-goals
  mask_all_text: true,
  mask_all_element_attributes: true,
  before_send: sanitize,          // §3.6
});
posthog.identify(user.id, { is_admin: user.is_admin });
```

- **Identity.** `identify` is called with the internal UUID and one
  person property, `is_admin`, which the project's internal-users
  filter (§3.1) keys on. `posthog.reset()` runs on sign-out.
- **Page views.** The router sends `$pageview` on each route change,
  with `$current_url` and `$pathname` replaced by the route pattern
  (`/episodes/:slug`, `/briefings/:id`, …). PostHog's web analytics
  views therefore work on page kinds, never on real URLs.
- **GPC.** If `navigator.globalPrivacyControl` is true, the SDK is not
  loaded.

#### 3.5 Server SDK

The `posthog` Python package sits behind one interface:

```python
class AnalyticsSink(Protocol):
    def capture(self, event: AnalyticsEvent, *, user_id: str | None,
                visitor_key: str | None) -> None: ...
    def delete_person(self, user_id: str) -> None: ...
    def shutdown(self) -> None: ...
```

There are two implementations: `PostHogSink` and `NullSink`. The app
builds one at startup, `NullSink` when analytics is off, and logs
`analytics.configured sink=posthog|null` so a missing key in production
shows up in the logs.

- `AnalyticsEvent` is a discriminated Pydantic union, one model per
  allowlisted event (FM-7). An unknown name or prop is a validation
  error at the call site, not a surprise in PostHog.
- `capture` drops the event if the user has opted out, or if the
  request carried `Sec-GPC: 1` and there's a user. Otherwise it hands
  the event to the SDK's in-memory queue.
- `capture` never raises and never blocks a request. Errors are logged
  at warning level and swallowed, on the analytics path only. Events are
  captured after the action they describe has succeeded.
- `shutdown()` flushes with a bounded timeout. It runs from the FastAPI
  lifespan exit and at CLI and worker process exit.
- GeoIP lookup is off (`disable_geoip=True`).
- Anonymous events use the daily visitor key as `distinct_id` and set
  `$process_person_profile: false`, so they never create a person.

**Visitor key.** `HMAC(server_secret ‖ UTC date, client_ip ‖ user_agent)`,
truncated to 16 bytes. It counts unique visitors today, is useless
tomorrow, cannot be reversed, and the raw IP is never stored or sent.
Under GPC, the event is still sent but with a random per-event id, so it
counts as a view and not as a visitor.

#### 3.6 Event allowlist

| Name | Emitted by | Props |
|---|---|---|
| `$pageview` | client, router | `path_kind` |
| `public_page_view` | server, #94 public routes | `kind`, `referrer_host` (browsers only; bots are not sent and are counted in the #94 §7 log line, which this phase adds) |
| `login_started` | server, `/api/auth/google/login` | `from_kind`: the public page kind parsed from `next`, or `none` |
| `login_completed` | server, OAuth callback | `next_kind` (whether the user is new is in `users`, not here) |
| `login_cancelled` | server, OAuth callback without a code (added 2026-10-22) | `reason`: Google's `error`, bucketed (`access_denied` = Cancel); see docs/analytics.md |
| `login_failed` | server, OAuth callback that did not sign the reader in (added 2026-10-22) | `reason`: `state_mismatch`, `account_disabled`, `signups_closed`, `error` |
| `search` | server, search route | `result_count`, `mode`; **never** the query text |
| `export` | server, #91 export routes | `artifact`, `format` |
| `share` | client, copy link / Web Share | `kind` |
| `narration_play` | client | `briefing_id` |
| `entity_open` | client | `entity_type` |
| `mcp_tool_call` | server, remote MCP HTTP runtime | `tool`, `ok` |

v1's client `sign_in_click` becomes the server's `login_started`. That
works because sign-in from a public page always goes through
`/api/auth/google/login?next=…`, so public pages need no SDK at all.

The client's `before_send` hook (`sanitize`) is the last check before
anything leaves the browser:

- drop any event whose name is not in the allowlist;
- drop any prop not in that event's allowlist, except the SDK's own
  device and browser fields;
- replace `$current_url`, `$pathname`, `$referrer` and
  `$initial_referrer` with route patterns or bare hosts;
- strip query strings and fragments everywhere;
- cap string props at 200 characters.

### 4. Surfaces

**Admin page.** `/analytics` shows Layer A only: signups, Activation,
Retention and Engagement from tables. It has a link to the PostHog
dashboard. It sits under the admin section in
[navigation.tsx](../thestill/web/frontend/src/constants/navigation.tsx),
so it appears in both the sidebar and the mobile drawer. It uses tables
and inline bars from the existing tokens; no charting library is added.
It shows aggregates only. Every percentage has its count next to it,
and cohorts or source groups with fewer than five users are shown
greyed out rather than hidden (decision D1).

**CLI.** `thestill analytics [--range 28d] [--json]` prints the Layer A
payload. In single-user mode it says analytics is off and exits 0.

**PostHog dashboard.** One dashboard, "thestill: product", with
Acquisition (public views by kind, referrers, `login_started` by
`from_kind`, completion rate `login_started` → `login_completed`) and
Engagement (WAU/MAU, feature counts, MCP calls by tool), all with the
internal-users filter on. Its insights are listed
in `docs/analytics.md`, so it can be rebuilt if the project is ever
recreated.

### 5. Privacy

- **What reaches PostHog:** the internal user UUID, `is_admin`, route
  patterns, allowlisted props, and the browser, OS and device type the
  SDK derives from the user agent. The IP address reaches PostHog's
  servers in transit and is discarded by the project setting.
- **What never does:** email, name, profile picture, Google id, search
  text, transcript or summary text, raw URLs, query strings.
- **No cookie and no browser storage.** Because the SDK stores nothing
  on the device, the UK and EU cookie rules (PECR and ePrivacy) don't
  apply and no consent banner is needed. A Playwright test checks that
  no `ph_` cookie or storage key exists after a signed-in session.
- **GPC.** The client doesn't load the SDK. The server sends no
  identified events for that request, and anonymous views carry no
  visitor key.
- **Per-user switch.** A new `users.usage_analytics_enabled` column
  (default `true`) backs a one-line Settings toggle: "Usage events: help
  improve thestill by recording which features you use". Off means
  `/api/auth/status` returns no key, so the client loads nothing, and
  the server sink drops every event carrying that user id. The server
  enforces the switch, not the client. Layer A still counts the user,
  because it reads product state, not events, and the Settings copy says
  so.
- **Deletion.** When account deletion exists, it calls
  `AnalyticsSink.delete_person` for that UUID, which uses PostHog's
  person deletion API. Phase 1 adds the method so it's ready.
- **Single-user mode or no key:** `NullSink`, no SDK, no `/analytics`
  page, no Settings toggle.
- **Privacy policy changes** ship in the same PR that turns PostHog on:
  - the summary line becomes "Public pages set no cookies and load no
    third-party scripts. We count visits on our server without
    identifying you";
  - a new "Usage analytics" section says what is sent, the lawful basis
    (legitimate interests), and how to opt out (the Settings toggle or
    GPC);
  - a new processor row: "PostHog, EU hosting | Product usage analytics
    | Internal account id, which features you use, pages by type,
    browser and device type; IP address processed in transit, not
    stored";
  - "We use no advertising or analytics cookies" stays true and stays.

### 6. Why PostHog, and why this way

| Option | For | Against |
|---|---|---|
| First-party event log (v1) | No vendor, nothing leaves | An event store, endpoint, rollups, retention job and charts to build and keep running, and it only answers questions listed in advance |
| PostHog Cloud, default snippet | Least setup | External script (CSP change), `ph_` cookie and probably a consent banner, autocapture of page text, blocked by content blockers |
| **PostHog Cloud EU, bundled, proxied, memory persistence, allowlist** | Exploration, funnels, dashboards for free; CSP and cookies unchanged; anonymous visitors run no vendor code | A processor to disclose; a project-settings checklist that lives outside the repo |
| PostHog self-hosted | Data stays on our box | A second product to run, upgrade and back up on one EC2 box |

Spec #75 rejected PostHog for LLM traces because those carry transcript
and summary content. Nothing in this spec's allowlist does, and the §3.6
sanitiser enforces that on the client.

## Failure modes

| Risk | Guard |
|---|---|
| Analytics slows or breaks a user-facing request | Server capture is a queue push after the action and never raises; the client SDK is lazy-loaded and fire-and-forget |
| PostHog is down or slow | Server SDK queue is bounded and drops when full; flush on shutdown has a timeout; nothing retries forever |
| A sensitive value leaks through an automatic SDK property (`$current_url` with `?q=`) | `before_send` sanitiser; Vitest on the sanitiser; Playwright asserts no query string in any `/ingest` payload (FM-7) |
| An SDK upgrade turns on a new capture feature by default | Project-level switches are off too; Playwright asserts that every `/ingest` request carries only allowlisted event names, and runs on every `posthog-js` bump |
| A cookie or storage key creeps in | Playwright asserts no `ph_` cookie or storage key after a signed-in session |
| Opt-out honoured only on the client | Server sink drops events for opted-out users; pytest posts with the flag off |
| Missing key in production goes unnoticed | `analytics.configured sink=…` startup log; `@live` check after deploy |
| Same fact counted twice from state and events | §1 rule; review checklist item for any new emitter (FM-6) |
| Retention looks worse because of blockers or opt-outs | Retention is Layer A only; PostHog numbers are labelled "of users who share usage" on the dashboard |
| A bug floods events and runs up a bill | Billing limit in PostHog; bots not sent; the SDK's client-side rate limit left on |
| `/ingest` is used as an open proxy | It forwards only to two fixed PostHog hosts; the key it accepts can only write events to our project |

## Non-goals

- Session replay, heatmaps, surveys. Transcripts and summaries are on
  screen almost everywhere, and replay masking is a promise, not a
  guarantee.
- Feature flags and experiments. They'd be a separate spec; the `/ingest`
  proxy would carry them without CSP changes.
- Autocapture.
- Any PostHog code on public pages or the sign-in page.
- Per-user activity views inside thestill.
- Email or notification analytics beyond the spec #51 delivery rows.
- LLM calls, tokens or cost: that is spec #75 and PR #351.
- A public stats page.

## Phases

| Phase | Scope | Ships alone? |
|---|---|---|
| 0 | `users.signup_source` + `briefings.opened_at` migration and backfill; set `signup_source` in the OAuth callback; `POST /api/briefings/{id}/opened` called from the briefing page; `AnalyticsRepository` (Layer A); `GET /api/dashboard/analytics`; `/analytics` admin page; `thestill analytics` | Yes; no vendor, answers the activation questions from the first deploy |
| 1 | PostHog project and settings checklist; config; `AnalyticsSink` + `PostHogSink` + `NullSink`; Caddy `/ingest`; lazy client SDK with sanitiser; `$pageview`; `login_started`, `login_completed`, `public_page_view` (plus the #94 §7 log line); visitor key; GPC; Settings switch and column; privacy policy changes; `docs/analytics.md` | Yes |
| 2 | Remaining emitters (`search`, `export`, `share`, `narration_play`, `entity_open`, `mcp_tool_call`); the PostHog dashboard | Yes |

## Testing

**pytest** (Postgres contract tests via `TEST_DATABASE_URL`, as spec #85
established):

- Funnel queries against a seeded fixture: known signups with mixed
  `signup_source` (including `null`), follows at known offsets,
  briefings with and without `opened_at`, reads; expected percentages
  and medians asserted exactly, overall and per source.
- `signup_source`: a new user whose `oauth_next` is `/episodes/x` gets
  `episode`; no cookie gets `direct`; a returning user's value never
  changes on a later login.
- `POST /briefings/{id}/opened`: first call sets `opened_at`, a second
  call leaves it unchanged, another user's briefing is 404, and a GET of
  the briefing never sets it.
- Migration backfill: `opened_at = listened_at` where set, `null`
  otherwise.
- Cohort table: a user active in week 4 but not 2 lands in the right
  cells.
- Each server emitter, with a recording fake sink: the right event and
  props after success, nothing after failure.
- `login_started`: `next=/episodes/x` → `from_kind=episode`; no `next` →
  `none`; a rejected `next` → `none`.
- Opted-out user and `Sec-GPC: 1`: nothing identified is captured.
- `PostHogSink` with a client that raises: the request still succeeds,
  and a warning is logged.
- `visitor_key`: same client, two dates → different keys; the key never
  equals or contains the IP.
- Bots: no `public_page_view` sent; the log line is still written.
- No key, or single-user mode: `NullSink`, `/api/auth/status` returns
  `analytics: null`, CLI exits 0 with the "off" message.
- CSP header unchanged on every page.
- `caddy validate` on the Caddyfile, in the existing deploy tests.

**Vitest:** the sanitiser strips query strings and fragments, maps paths
to route patterns and drops unknown events and props. The SDK is not
imported on public routes, when the key is null, or under GPC. `identify`
carries the UUID and `is_admin` only. The briefing page calls `opened`
once per briefing after it has loaded, also under StrictMode.
Cohorts under five users render greyed out with their count. The admin
nav shows `/analytics` only for admins, in both the sidebar and the
drawer.

**Playwright:** with a fake key and `/ingest` intercepted, a signed-in
session that searches with a query string, opens a briefing and exports
sends only allowlisted event names, with no query string and no
transcript text in any payload, and leaves no `ph_` cookie or storage
key. A public page load requests no PostHog chunk and nothing under
`/ingest`.

## Phase 0 implementation notes

- The tables are `user_briefings` and `user_episode_inbox`; earlier
  drafts called them `briefings` and `inbox_entries`.
- Postgres gets the columns from alembic `0017_signup_source_briefing_opened`
  (DDL mirrored in `SCHEMA_SQL`, backfill only in the migration); SQLite
  adds them in `SqlitePodcastRepository`'s column guards.
- The page-kind classifier lives in `thestill/utils/page_kind.py` so the
  Phase 1 PostHog props (`from_kind`, `next_kind`, `kind`) reuse it.
- `AnalyticsService` owns range, window and availability for both the
  route and the CLI. On SQLite the repository is
  `UnavailableAnalyticsRepository`, and both surfaces report "off".
- Briefing steps use a 7-day horizon, like the other week-one steps, so
  they can be maturity-gated.
- Retention weeks are relative to each user's own signup (week k is days
  7k to 7k+7), so a cell fills only once the whole week has passed.

## Phases 1–2 implementation notes

What was built differs from the sections above in these places:

- **Public page views use a first-party beacon.** After the first load the
  public site navigates in the browser, so the server never sees later
  views. `PublicApp` posts each route change to
  `POST /api/public/pageview` (`{path, referrer}`, referrer on the first
  view only). The server derives the kind with `public_page_kind`, logs
  `public_page_view` (the spec #94 §7 line), and sends browsers, not
  crawlers or link previews (`utils/user_agent.py`), to PostHog. Still no
  third-party code on public pages.
- **Not built:** `export` (spec #91 has no routes yet) and `narration_play`
  (no narration audio player in the web app).
- **`search`** is counted only on `/api/search/corpus` and not for
  in-episode expansion (`episode_id`); command-bar quick search fires per
  keystroke and is not counted.
- **`mcp_tool_call.ok`** is false for an error result and for a result whose
  JSON says `"success": false` or carries an `error`, because most tool
  failures come back as ordinary results.
- **`delete_person` is not on the sink.** PostHog's Python SDK has no
  deletion call; it needs the REST API and a personal API key. Add both
  when account deletion exists.
- **Caddy strips `Cookie`, `Authorization` and `Referer`** on `/ingest`:
  the browser sends its session cookie with same-origin requests, and the
  page URL (query string included) as `Referer`, which would bypass the
  event filter.
- **Sign-in events are anonymous, one id per attempt.** `login_started`
  happens before there is an account, so `login_completed` uses the same
  id rather than the user's: an HMAC of the OAuth `state`, which both ends
  know. The funnel joins without a cookie, and nothing links the visitor
  key or the account to it. Opted-out readers send no completion.
- **Shared PostHog project ready.** Caddy also strips `X-Forwarded-For`,
  so no reader IP reaches PostHog whatever the project's IP setting, and
  every event carries `app: "thestill"` (server events have no `$host`).
  thestill can share a project with another site without touching its
  settings; see [docs/analytics.md](../docs/analytics.md).
- **Remote MCP honours GPC:** the guard stores `Sec-GPC` on the request
  state and the tool-call event carries it.
- **The browser SDK is bootstrapped with the user id**, so memory
  persistence never mints an anonymous id to merge, and it sends no
  `$identify`. It sends one `$set` per load with `is_admin` and nothing
  else (sign-in events are anonymous, so they cannot carry it), for the
  internal-users filter.
- **Stopping is an in-memory flag, not posthog-js opt-out**, because the
  SDK writes opt-out consent to `localStorage` even with memory
  persistence. `before_send` drops everything while stopped.
- **The browser filter allowlists the SDK's own props too.** posthog-js
  adds `$session_entry_url`, `$prev_pageview_pathname` and similar to every
  event; anything not on the list is dropped, URL props are rewritten.
- **posthog-js drops headless browsers as bots** (`navigator.webdriver` and
  a `HeadlessChrome` brand); `tests/usage-events.spec.ts` masks both.
- **Config:** `ANALYTICS_ENABLED` defaults to `true` and is ignored in
  single-user mode; with no `POSTHOG_PROJECT_KEY` the sink is `NullSink`.
- **Settings switch:** `PATCH /api/auth/me` with `usage_analytics_enabled`;
  the handler now changes only the fields sent, so the switch cannot clear
  a region. Column via alembic `0018`.

Operator steps (PostHog project settings, SSM key, dashboard) are in
[docs/analytics.md](../docs/analytics.md).

## Decisions

Settled on 2026-10-16; these were the open questions in the first v2
draft.

1. **D1. Small cohorts are shown, not hidden.** At current user counts a
   cutoff would hide almost every cohort. Every percentage carries its
   count, and groups under five users are greyed out (§4).
2. **D2. The opt-out stops events, not table-based metrics.** Follows,
   reads and briefings are product state the service needs to run, so
   Layer A counts every user. The Settings copy says so in one line.
3. **D3. Follows and briefings are not mirrored into PostHog.** The
   reason to mirror was activation by acquisition source. That is
   answered instead by `users.signup_source` (§2): one fact, stored
   once, unaffected by ad blockers or opt-outs. For the same reason
   `login_completed` carries no `new_user` prop.
4. **D4. "Opened" is state, not an event.** `user_briefings.opened_at`
   is set on first web open, and on first narration play once briefing
   audio ships (§2). The v1
   `briefing_open` event is dropped. `narration_play` stays an event,
   because it counts every play, not just the first. `listened_at`
   keeps its meaning: the user pressed "Mark listened".
5. **D5. Admin events are sent and filtered inside PostHog.** The
   project's internal-users filter on `is_admin = true` applies to every
   insight by default (§3.1), so production can be checked end to end
   without the operator's own use skewing the numbers.
