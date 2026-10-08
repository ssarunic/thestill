# Product Analytics

> **Status:** 📝 Draft
> **Created:** 2026-10-08
> **Updated:** 2026-10-08
> **Priority:** Medium, but Phase 0 must land before any launch driven by spec #94: a launch nobody can measure teaches nothing.
> **Author:** Product & Engineering
> **Related:** [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md) (CSP `connect-src 'self'`), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (read state), [#42 robustness-and-failure-mode-hardening](42-robustness-and-failure-mode-hardening.md), [#50 scheduled-briefings](50-scheduled-briefings.md) / [#84 scheduled-only-briefings](84-scheduled-only-briefings.md), [#75 llm-call-tracing](75-llm-call-tracing.md) (rejected PostHog for traces), [#78 remote-mcp-access](78-remote-mcp-access.md), [#80 share-to-thestill](80-share-to-thestill.md) (`next` through login), [#86 quality-feedback-loop](86-quality-feedback-loop.md), [#94 public-pages](94-public-pages.md) (server-side public view log)

---

## Executive summary

Thestill has no product analytics. Nobody can answer how many people
signed up last week, how many of them followed a show, how many opened
their first briefing, or how many came back. The frontend loads no
tracker, the server counts nothing, and the one earlier decision in this
area (spec #75) rejected PostHog for LLM traces on confidentiality
grounds without saying anything about page views or funnels.

Most of the answers already exist as first-party state. A signup is a
`users` row, a follow is a `podcast_followers` row, a briefing is a
`briefings` row with `listened_at`, a read episode is an inbox entry
with `state_changed_at`. The product's own tables are the most accurate
funnel there is, and they need no collection at all.

So the design has two layers:

| Layer | Source | Answers | New collection |
|---|---|---|---|
| A. Derived funnel | Existing tables | Signups, activation steps, cohort retention, briefing open rate | None |
| B. Event log | New `product_events` table, first-party | Page views, public → sign-in conversion, feature use, MCP tool calls | A small allowlisted event stream |

Both surface on one admin page (`/analytics`) and one CLI command
(`thestill analytics`). No third-party script, no new cookie, no change
to the Content Security Policy, nothing leaves the server. Analytics is
off in single-user mode: a self-hoster is the only user of their own
instance and there is nothing to learn and nobody to phone.

The spec deliberately does **not** adopt PostHog or any vendor now. A
server-side export sink is sketched as an optional last phase, to be
built only if the admin page proves insufficient.

## Outcomes

| # | Outcome | Verified by |
|---|---|---|
| O1 | An admin can read, for any 7/28/90-day window: signups, % who followed ≥1 show within 7 days, % whose first briefing was opened, weekly cohort retention | Postgres contract tests on the funnel queries, `@live` check after deploy |
| O2 | Public pages (spec #94) report views, referrers and sign-in clicks, and how many of those visitors became users | pytest on the event endpoint and the `next` join |
| O3 | Feature use is countable: search, export, share, narration play, entity page opens, remote MCP tool calls | pytest per emitter |
| O4 | Nothing identifies a person beyond the internal user id; anonymous visitors get no persistent identifier; CSP is unchanged | header test, visitor-key rotation test, schema review |
| O5 | A user can switch usage events off in Settings and the switch is honoured server-side | pytest + Vitest |
| O6 | Single-user installs collect nothing and expose no analytics surface | pytest in single-user mode |

## Current state

- **Frontend.** No analytics, tracker, beacon or pixel anywhere under
  `thestill/web/frontend/src`. The CSP at
  [security_headers.py:52-64](../thestill/web/middleware/security_headers.py#L52-L64)
  pins `connect-src 'self'` and `script-src 'self'` plus YouTube, so a
  vendor script could not load without a CSP change in any case.
- **Server.** [logging_middleware.py](../thestill/web/middleware/logging_middleware.py)
  logs every request with path, status and duration under a
  `request_id`. Those logs go to CloudWatch in production. They are
  operational, not product data: no user id, no page semantics, and the
  retention and query cost of CloudWatch make funnels across weeks
  impractical.
- **State with timestamps already present.** `users.created_at`,
  `users.last_login_at`; `podcast_followers.created_at`;
  `inbox_entries.delivered_at` and `state_changed_at` with
  `state ∈ {unread, read, saved, dismissed}`; `briefings.created_at`,
  `listened_at`, `episode_count`; `mcp_tokens.created_at`,
  `last_used_at`. No `user_episode_progress` yet (spec #90, unbuilt).
- **No account deletion** exists. When it does, analytics rows must go
  with the account (see §6).
- **Admin surface.** [api_dashboard.py](../thestill/web/routes/api_dashboard.py)
  serves `/stats`, `/activity`, `/narration` behind `require_admin` to
  the Status page. Admin nav items live in
  [navigation.tsx](../thestill/web/frontend/src/constants/navigation.tsx)
  and render in both the sidebar and the mobile drawer.
- **Repositories are dual** (SQLite and Postgres implementations under
  [repositories/](../thestill/repositories/)). Hosted multi-user runs on
  Postgres; single-user runs on SQLite.

## Questions the system must answer

Each question names its layer and source so the implementation cannot
drift into collecting things it does not need.

| Area | Question | Layer | Source |
|---|---|---|---|
| Acquisition | Public page views per day by kind (landing, episode, podcast, entity, top), excluding bots | B | server-side `public_page.view` from spec #94 §7, written to the event table |
| Acquisition | Top referrer hosts for public views | B | same event, `referrer_host` |
| Acquisition | Sign-in clicks from public pages, and which page | B | client event `sign_in_click` with the page kind |
| Acquisition | Signups per day, and how many arrived through a public page | A + B | `users.created_at`; the `next` path recorded on the login callback (spec #80 §3 plumbing) |
| Activation | % of new users who followed ≥1 podcast within 24 h and 7 d, and median time to first follow | A | `podcast_followers` |
| Activation | % whose first briefing was generated, and % who opened it (web open or narration listened) | A + B | `briefings.created_at`, `listened_at`; client `briefing_open` |
| Activation | % who read ≥3 episodes in their first week | A | `inbox_entries.state = read`, `state_changed_at` |
| Engagement | Weekly and monthly active users | B | any client or server event with a `user_id` in the window |
| Engagement | Briefings opened ÷ briefings generated, per week | A + B | `briefings` + `briefing_open` |
| Engagement | Episodes read per active user per week | A | inbox read transitions |
| Engagement | Searches, exports, shares, narration plays, entity page opens per week | B | client events |
| Engagement | Remote MCP tool calls per user per week, by tool | B | server event from the MCP HTTP runtime |
| Retention | For each signup week: % active in weeks 1, 2, 4, 8 | A + B | cohort = `users.created_at`; active = any event or read transition |
| Quality | Spec #86 reports per 1,000 episode reads | A | #86 report table ÷ read transitions |

Anything not in this table is not collected. Adding a row means editing
this spec first (constitution §10).

## Design

### 1. Rule: state first, events second

If a fact already lives in a table with a timestamp, it is read from
that table and **never** emitted as an event as well. Two records of the
same fact drift (FM-6) and the table is the one the product trusts.
Events exist only for things that leave no durable state: a view, a
click, a search, an export, a tool call.

### 2. Layer A: the derived funnel

A new `AnalyticsRepository` with a Postgres implementation and a
single-user stub that reports "unavailable". It exposes four read
methods, each one SQL statement over existing tables, parameterised by a
window:

- `signups(window)`: daily counts, plus the share whose login callback
  carried a public-page `next`.
- `activation(window)`: for users created in the window, the follow /
  briefing-generated / briefing-opened / three-reads steps with counts,
  percentages and median hours to each step.
- `cohorts(weeks)`: signup-week rows × activity-week columns.
- `engagement(window)`: reads per active user, briefings opened ÷
  generated, follows per user.

Results are plain Pydantic models (constitution §5). Queries are indexed
by columns that already exist; the one addition is an index on
`inbox_entries (state, state_changed_at)` if the planner wants it, decided
by `EXPLAIN` on a production-sized copy, not guessed.

### 3. Layer B: the event log

**Table** (Postgres only; migration under
[migrations/versions](../thestill/migrations/versions/)):

```sql
product_events (
  id            bigserial primary key,
  occurred_at   timestamptz not null,   -- server clock, never the client's (FM-3)
  name          text        not null,   -- allowlisted, see below
  user_id       uuid        null references users(id) on delete cascade,
  visitor_key   text        null,       -- anonymous only, daily-rotating, see §5
  path_kind     text        null,       -- route pattern, e.g. "episode", never a raw URL
  props         jsonb       not null default '{}',
  referrer_host text        null,
  ua_class      text        not null,   -- browser | bot | preview
  request_id    text        null
)
index (name, occurred_at)
index (user_id, occurred_at) where user_id is not null
```

**Event allowlist.** A Pydantic `EventName` enum and a per-event props
model. Unknown names and unknown prop keys are rejected (FM-7: the client
is untrusted input), string props are capped at 200 characters and the
whole `props` object at 1 KB.

| Name | Emitted by | Props |
|---|---|---|
| `page_view` | client, on route change | `path_kind`; for episode/podcast pages the slug; **never** the query string |
| `public_page_view` | server, spec #94 catch-all | `kind`, `referrer_host`, `ua_class` |
| `sign_in_click` | client | `from_kind` |
| `login_completed` | server, OAuth callback | `next_kind` (the public page kind in `next`, or `none`), `new_user` |
| `briefing_open` | client, briefing page mount | `briefing_id` |
| `search` | server, search route | `result_count`, `mode`; **never** the query text |
| `export` | server, spec #91 export routes | `artifact`, `format` |
| `share` | client, copy link / Web Share | `kind` |
| `narration_play` | client | `briefing_id` |
| `entity_open` | client | `entity_type` |
| `mcp_tool_call` | server, MCP HTTP runtime | `tool`, `ok` |

Server-emitted events are written from the service or route that
already handles the action, inside the request, after the action
succeeded. Client events go through one endpoint.

**Endpoint.** `POST /api/events`, session or anonymous, body = a batch
of at most 20 events, each `{name, props}`. The server stamps
`occurred_at`, `user_id` (from the session), `visitor_key` (anonymous
only), `ua_class` and `request_id`; the client cannot set any of those.
Response is `202` with `{accepted, rejected}`; a malformed batch is
`400` and never `500`. Rate-limited with a dedicated bucket
(`RATE_LIMIT_EVENTS_MAX`, default 60 per minute) keyed by user id or
client IP. The client sends with `fetch(…, {keepalive: true})`, falling
back to `navigator.sendBeacon` on `pagehide`, swallows every error, and
never blocks or delays navigation. Analytics is best effort by
definition; this is the one place FM-1 does not apply to the client,
while the server still logs rejected batches at warning with a count.

**Rollups.** A nightly job (same scheduler as briefings) writes
`product_daily_rollups (day, name, path_kind, ua_class, count,
unique_users, unique_visitors)` for the previous day only, idempotent per
`(day, name, path_kind, ua_class)`. Today's numbers are computed live
from raw rows. Raw events older than `ANALYTICS_RETENTION_DAYS` (default
180) are deleted by the existing cleanup job; rollups are kept.

### 4. Surfaces

**Admin API.** `GET /api/dashboard/analytics?range=7d|28d|90d` behind
`require_admin`, returning the four Layer A models plus Layer B
aggregates: views by kind and day, referrers, sign-in clicks, feature
counts, MCP calls by tool, WAU/MAU. Cached in-process for ten minutes.
Aggregates only: the API never returns a per-user row, and no endpoint
lists one user's events. An operator who needs that has `psql`.

**Admin page.** `/analytics` under the admin section in
[navigation.tsx](../thestill/web/frontend/src/constants/navigation.tsx),
so it appears in both the sidebar and the mobile drawer. Four sections
in the order the questions table lists them: Acquisition, Activation,
Engagement, Retention. Tables and inline bars from the existing tokens;
no charting library is added to the bundle. The cohort table is the only
dense element and gets a horizontal scroll on phones.

**CLI.** `thestill analytics [--range 28d] [--json]` prints the same
payload, for the operator's terminal and for pasting into a weekly note.
In single-user mode it says analytics is off and exits 0.

### 5. Privacy

- **No third parties.** No script, pixel, beacon or DNS lookup to any
  vendor. CSP stays byte-identical; a test asserts it.
- **No new cookie.** Signed-in events ride the existing session.
  Anonymous visitors are counted with a `visitor_key` =
  `HMAC(server_secret ‖ UTC date, client_ip ‖ user_agent)` truncated to
  16 bytes. It lets "unique visitors today" be counted, is useless
  tomorrow, cannot be reversed, and the raw IP is never stored. Nothing
  about this requires a consent banner, and no banner is added.
- **Global Privacy Control.** A request with `Sec-GPC: 1` is counted as
  a view with `visitor_key = null`.
- **Bots** (`ua_class = bot|preview`, classified as in spec #94 §7) are
  stored, because crawl volume is useful, and excluded from every
  product metric by default.
- **No query text, no raw URLs, no emails.** Search stores a result
  count, page views store a route kind and slug, users are the internal
  UUID.
- **Per-user switch.** Settings gains "Usage events: help improve
  thestill by recording which features you use" (default on, one line,
  no dark pattern). Off means the client sends nothing and the server
  drops any event carrying that user id (the toggle is the server's
  truth, not the client's). Layer A still counts the user, because it
  reads product state, not events; the Settings copy says so.
- **Deletion.** `user_id` is `on delete cascade`, so account deletion,
  when it exists, removes the person's events. Rollups keep counts only.
- **Single-user mode.** `ANALYTICS_ENABLED` defaults to `multi_user`.
  When off: no table writes, no endpoint, no nav item, no client sends.

### 6. Why not a vendor now

| Option | For | Against |
|---|---|---|
| First-party (this spec) | Fits CSP and the #75 stance, no PII leaves, no consent surface, funnel from real tables | Admin page is hand-built; no session replay, no self-serve exploration |
| PostHog cloud, client SDK | Rich exploration, replay, flags | Needs CSP `connect-src` + `script-src` changes, a cookie and probably a banner, sends user ids and paths to a vendor, contradicts the #75 reasoning |
| PostHog self-hosted | Same tooling, data stays in | A second product to run, upgrade and back up on one EC2 box |
| Server-side export to a vendor | Keeps CSP and the client untouched; identities hashed | Still a vendor; only worth it once the questions outgrow the admin page |

Decision: first-party, with the last row kept as Phase 3 behind an
`AnalyticsSink` interface so adding it later is additive. The trigger
for building it is a concrete question the admin page cannot answer, not
a feeling that a dashboard should exist.

## Failure modes

| Risk | Guard |
|---|---|
| Event writes slow or break a user-facing request | Server-emitted events are one insert after the action; client batches are fire-and-forget; any insert error is logged at warning and swallowed on the analytics path only |
| Client clock skew produces events in the future or past | `occurred_at` is the server clock, always (FM-3) |
| Unbounded or hostile event payloads | Allowlisted names, per-event prop models, 1 KB cap, 20-event batches, rate limit (FM-7) |
| Same fact counted twice from state and events | §1 rule; review checklist item for any new emitter (FM-6) |
| Rollup runs before the day is closed and freezes partial counts | Rollup covers the previous UTC day only and is idempotent per key (FM-2) |
| Visitor key becomes a tracking identifier | Daily rotation with the date in the HMAC input; test asserts two dates give different keys for the same client |
| Opt-out honoured only on the client | Server drops events for opted-out user ids; test posts with the flag off |
| Analytics table grows without bound | Retention job + rollups; test deletes rows older than the window and keeps rollups |
| Admin page becomes a per-user surveillance tool | API returns aggregates only; no per-user endpoint exists to misuse |

## Non-goals

- Session replay, heatmaps, feature flags, A/B testing.
- Any third-party script in the browser.
- Per-user activity views for admins.
- Email or notification analytics beyond the spec #51 delivery rows that
  already exist.
- Counting LLM calls, tokens or cost; that is spec #75.
- A public stats page.

## Phases

| Phase | Scope | Ships alone? |
|---|---|---|
| 0 | `AnalyticsRepository` (Layer A), `GET /api/dashboard/analytics` with the four funnel models, `/analytics` admin page with Activation + Retention, `thestill analytics` | Yes; needs nothing from #94 and answers the activation questions today |
| 1 | `product_events` table, `POST /api/events`, client `page_view` + `sign_in_click` + `briefing_open`, server `public_page_view` (from #94) + `login_completed`, Acquisition section, WAU/MAU | Yes; depends on #94 Phase 1 for the public kinds and on the `next` plumbing for conversion |
| 2 | Remaining emitters (`search`, `export`, `share`, `narration_play`, `entity_open`, `mcp_tool_call`), Engagement section, Settings switch, rollups + retention | Yes |
| 3 | `AnalyticsSink` interface and an optional server-side vendor export with hashed ids, flag-gated | Only if a question demands it |

## Testing

**pytest** (Postgres contract tests via `TEST_DATABASE_URL`, as spec #85
established):

- Funnel queries against a seeded fixture: known signups, follows at
  known offsets, briefings with and without `listened_at`, reads;
  expected percentages and medians asserted exactly.
- Cohort table: a user active in week 4 but not 2 lands in the right
  cells.
- `POST /api/events`: unknown name → rejected, counted in `rejected`;
  21 events → 400; 1.1 KB props → rejected; server stamps time and user
  and ignores any client-supplied `user_id` or `occurred_at`.
- Opted-out user: batch accepted with `202` but nothing written.
- `visitor_key`: same client, two dates → different keys; key never
  equals or contains the IP; `Sec-GPC: 1` → null key.
- Bots excluded from WAU, included in raw views.
- Rollup: run twice for the same day → one row; today is never rolled.
- Retention job: rows older than the window gone, rollups intact.
- Single-user mode: endpoint 404, repository stub, CLI exits 0 with the
  "off" message.
- CSP header unchanged on every page.

**Vitest**: route change emits one `page_view` with a route kind and no
query string; the Settings switch posts the flag and stops the client
emitter; the admin nav shows `/analytics` only for admins in both the
sidebar and the drawer.

## Open questions

1. **Minimum cohort size.** Should percentages hide below, say, five
   users in a cohort to avoid reading too much into one person? Default
   here: show the count next to every percentage and let the reader
   judge.
2. **Layer A and the opt-out.** The switch stops events but not derived
   metrics. Is that the right line, and is the Settings copy honest
   enough about it?
3. **Retention window.** 180 days of raw events, rollups forever. Long
   enough for a launch retrospective, short enough to stay small?
4. **Narration listened.** `briefings.listened_at` is set by an explicit
   call today; should `narration_play` set it too, so Layer A and Layer
   B agree on "opened"?
