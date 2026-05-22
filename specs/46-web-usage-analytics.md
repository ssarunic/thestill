# Web Usage Analytics (PostHog)

> **Status:** 📝 Draft (2026-05-22)
> **Created:** 2026-05-22
> **Updated:** 2026-05-22
> **Author:** Engineering
> **Related:** [06-authentication.md](06-authentication.md) (user identity, `multi_user`, `/auth/status`), [22-floating-media-player.md](22-floating-media-player.md) (the deferred play/pause/ended hook this spec wires up), [29-per-user-inbox-fanout.md](29-per-user-inbox-fanout.md) (read/saved/dismissed engagement signals; Open Q #4), [28-corpus-search-and-entities.md](28-corpus-search-and-entities.md) (search + entity surfaces to instrument), [43-aws-hosting.md](43-aws-hosting.md) (where the hosted `POSTHOG_KEY` lives)

---

## Executive Summary

When `thestill.me` goes public we want to learn **which features earn their
keep** — what people actually open, play, search, and abandon — so the roadmap
follows real usage instead of guesses. This spec adds **product analytics for
the web app via PostHog**, and nothing more.

Two hard constraints shape the whole design:

1. **Analytics is the operator's, not a shipped feature.** It is not exposed to
   end users and there is no per-user analytics UI. It exists so *we* can read
   usage on the hosted instance.
2. **Self-hosted installs stay silent by default.** thestill is an
   Apache-licensed, self-hostable app. A self-hoster should send *zero*
   telemetry without lifting a finger.

The mechanism that satisfies both at once: **the PostHog project key is never
baked into the build.** The server hands it to the frontend at runtime, and
only ever has a key to hand when `POSTHOG_KEY` is set in the environment —
which happens only on `thestill.me`. No key → `posthog-js` never initializes →
off by default, with nothing to configure and nothing to opt out of.

This is deliberately **not** the same thing as the structlog observability
already documented in [logging-configuration.md](../docs/logging-configuration.md)
("MCP Tool Usage Analytics", "API Performance Monitoring"). That answers *is the
service healthy and how is the API hit*. This spec answers *which product
features users value and where they drop off* — funnels, retention, and
feature-usage breakdowns that endpoint-level request logs can't express
(SPA route changes, play/pause, search refinement, and abandoned flows often
never hit the API at all).

---

## Table of Contents

1. [Goals & Non-Goals](#goals--non-goals)
2. [Current State](#current-state)
3. [Design](#design)
   - [Off-by-default via runtime key delivery](#off-by-default-via-runtime-key-delivery)
   - [Frontend initialization](#frontend-initialization)
   - [Identity](#identity)
   - [Event taxonomy](#event-taxonomy)
   - [Privacy posture](#privacy-posture)
   - [CSP / network](#csp--network)
4. [Configuration](#configuration)
5. [Implementation Phases](#implementation-phases)
6. [Testing](#testing)
7. [Open Questions](#open-questions)
8. [Non-Goals (restated)](#non-goals-restated)

---

## Goals & Non-Goals

### Goals

- Capture web product-usage events on the hosted instance into a PostHog
  project owned by the operator.
- Answer concrete questions: which pages/features get used, what the
  add-podcast → first-play funnel looks like, week-1 retention, and which
  features see ~no usage and are candidates for cutting.
- Zero telemetry from self-hosted installs unless the operator explicitly opts
  in by setting their own key.
- Reuse existing seams: `/auth/status` (already fetched on mount),
  `AuthContext` (user identity), `PlayerContext` (play/pause/ended),
  `utils/config.py` (env-driven settings).

### Non-Goals

- **No CLI analytics.** This is web-only.
- **No per-user analytics dashboard or any user-facing surface.**
- **No server-side observability rework.** structlog HTTP/MCP logging
  ([logging-configuration.md](../docs/logging-configuration.md)) stays as-is;
  this spec does not route product events through it.
- **No session replay** and **no PostHog autocapture** in v1 (see
  [Privacy posture](#privacy-posture)).
- **No licensing / anti-rehosting work** — that is a separate concern and is
  out of scope here.

---

## Current State

- **Frontend** — React 19 + `react-router-dom` 7 + `@tanstack/react-query` +
  Vite + Tailwind ([package.json](../thestill/web/frontend/package.json)).
  **No analytics library installed.** Providers are mounted in
  [main.tsx](../thestill/web/frontend/src/main.tsx)
  (`QueryClientProvider` → `BrowserRouter` → `AuthProvider` → `ToastProvider`).
- **Identity** — `AuthContext`
  ([AuthContext.tsx](../thestill/web/frontend/src/contexts/AuthContext.tsx))
  fetches `/api/auth/status` on mount and exposes `user` (with stable `id` and
  `region`), `isMultiUser`, `isAuthenticated`. Single-user self-host has an
  implicit user; multi-user (`thestill.me`) authenticates via Google OAuth
  ([06-authentication.md](06-authentication.md)).
- **`/auth/status`** ([auth.py:154](../thestill/web/routes/auth.py#L154))
  already returns `{ multi_user, authenticated, user }` via the standard
  `api_response` envelope — the natural place to also hand down the analytics
  config.
- **Player** — `PlayerContext`
  ([PlayerContext.tsx:35](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L35))
  exposes `play`/`pause` and owns the `<audio>` element; the deferred
  "Analytics / logging hook point" lives at
  [22-floating-media-player.md:267](22-floating-media-player.md).
- **Config** — `utils/config.py` reads settings from env with simple defaults;
  `multi_user` is the established pattern
  ([config.py:186](../thestill/utils/config.py#L186),
  [config.py:477](../thestill/utils/config.py#L477)).
- **CSP** — `security_headers` middleware
  ([security_headers.py](../thestill/web/middleware/security_headers.py)) sets a
  Content-Security-Policy that will block PostHog unless its host is
  allowlisted.

There is **no existing analytics plan**. Prior mentions are incidental hooks:
the deferred player event ([#22](22-floating-media-player.md)), an open question
about counting dismissed deliveries
([29-per-user-inbox-fanout.md:516](29-per-user-inbox-fanout.md)), and the inbox
`source` enum kept partly "for analytics"
([31-import-arbitrary-episodes.md:511](31-import-arbitrary-episodes.md)).

---

## Design

### Off-by-default via runtime key delivery

The PostHog project key is **not** a build-time constant (`VITE_*`) — baking it
into the open-source bundle would ship the operator's key to every self-hoster
and couple the key to a rebuild. Instead:

1. The server reads `POSTHOG_KEY` / `POSTHOG_HOST` from the environment
   (empty by default).
2. `/auth/status` gains an `analytics` block in its response:

   ```jsonc
   {
     "multi_user": true,
     "authenticated": true,
     "user": { "id": "…", "region": "US", … },
     "analytics": {
       "enabled": true,                       // == bool(POSTHOG_KEY)
       "key": "phc_…",                        // null when disabled
       "host": "https://us.i.posthog.com"
     }
   }
   ```

3. The frontend initializes PostHog **iff** `analytics.enabled && analytics.key`.

On a self-hosted box no `POSTHOG_KEY` is set → `enabled: false`, `key: null` →
the frontend never loads or initializes `posthog-js`. **No opt-out needed; the
default is silence.** A self-hoster who *wants* their own analytics simply sets
their own `POSTHOG_KEY` (explicit opt-in).

Reusing `/auth/status` (already fetched once on mount) means no new endpoint and
no extra round-trip. If we'd rather not overload auth, the alternative is a tiny
public `GET /api/config` returning only the analytics block — noted as an open
question.

### Frontend initialization

- Add `posthog-js` to [package.json](../thestill/web/frontend/package.json).
- New module `src/analytics/posthog.ts`:
  - `initAnalytics(config)` — calls `posthog.init(key, { api_host, … })` once,
    guarded so it is a no-op when disabled or already initialized.
  - `track(event, props?)` — thin wrapper that no-ops when uninitialized, so
    call sites never need to null-check.
  - `identify(userId, props?)` / `resetIdentity()`.
- Initialize from `AuthContext` once `/auth/status` resolves (the context
  already holds the response). This keeps init after we know identity and
  whether analytics is even enabled — no flash of un-init'd tracking.
- **Pageviews** are captured manually (autocapture is off): a small effect in
  [App.tsx](../thestill/web/frontend/src/App.tsx) watching `useLocation()` fires
  `track('$pageview', { path })` on route change. (PostHog's automatic
  `$pageview` only fires on full loads, which an SPA rarely does.)

### Identity

- **Anonymous** (PostHog's generated distinct id) before login.
- On auth resolve with a user, `identify(user.id, { region, multi_user })`.
  Person properties stay minimal — `region` is already non-PII and useful for
  cohorting.
- On logout, `resetIdentity()` so a shared browser doesn't blend sessions
  (wire into `AuthContext.logout`).
- **Email is intentionally not sent** as a person property in v1 (PII
  minimization). Identifying by opaque `user.id` is enough to build funnels and
  retention. Revisit only if operator-side debugging needs it (open question).

### Event taxonomy

Explicit, named events — small, intentional, and privacy-safe. v1 set:

| Event | Fired from | Key props |
|---|---|---|
| `$pageview` | `App.tsx` route effect | `path` |
| `episode_play` | `PlayerContext.play` ([PlayerContext.tsx:35](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L35)) | `episode_id`, `podcast_id`, `source` (inbox/detail/search) |
| `episode_pause` | `PlayerContext.pause` | `episode_id`, `position_s` |
| `episode_complete` | `<audio>` `ended` | `episode_id` |
| `search_performed` | `CommandBar.tsx` / `SearchResults.tsx` | `query_len`, `result_count` (**not** the query text) |
| `entity_viewed` | `Entities.tsx` | `entity_type` (person/company/topic) |
| `digest_opened` | `DigestDetail.tsx` | `digest_id` |
| `briefing_opened` | `BriefingDetail.tsx` | `briefing_id` |
| `import_submitted` | `ImportEpisodeModal.tsx` | `resolver` (youtube/rss/substack/audio) |
| `podcast_added` | `AddPodcastModal.tsx` | `method` (search/paste) |
| `podcast_followed` | `PodcastDetail.tsx` | `podcast_id` |
| `inbox_action` | `Inbox.tsx` | `action` (read/saved/dismissed) — answers [#29 Open Q #4](29-per-user-inbox-fanout.md) |

Conventions: `snake_case` event names; never put free text (search queries,
episode titles, transcript content) into properties — only IDs, enums, and
counts. New events are added to this table in the same PR that introduces them.

### Privacy posture

- **Autocapture: off.** A transcript/reader app would otherwise hoover up
  on-page text and input values. Explicit events only — lighter, intentional,
  and safe.
- **Session replay: off** (privacy + cost).
- **No PII in event properties or person properties** beyond opaque `user.id`
  and `region`. This matches the project's existing discipline
  (`redact_mapping`, `log_safety`).
- Honor **Do Not Track** (skip init when `navigator.doNotTrack` is set) —
  cheap goodwill, open question whether to enable.
- A short privacy note on `thestill.me` disclosing PostHog usage.

### CSP / network

The `security_headers` middleware
([security_headers.py](../thestill/web/middleware/security_headers.py)) must
allowlist the PostHog host in `connect-src` (and `script-src` if loading the JS
from PostHog's CDN rather than bundling). **Only widen the CSP when
`POSTHOG_KEY` is set** so self-hosted CSP stays tight. Without this, events
silently fail to send — call it out in the Phase 0 acceptance check.

---

## Configuration

New env vars (all empty/off by default), following the
[config.py](../thestill/utils/config.py) pattern:

| Env var | Default | Meaning |
|---|---|---|
| `POSTHOG_KEY` | `""` | PostHog **project** key (`phc_…`). Empty ⇒ analytics fully off. Set only on `thestill.me`. |
| `POSTHOG_HOST` | `https://us.i.posthog.com` | Ingestion host. EU residency ⇒ `https://eu.i.posthog.com`. |

Derived: `analytics_enabled = bool(POSTHOG_KEY)`. Add the fields near the auth
block in `config.py` and surface the `analytics` object from `/auth/status`.
Document both in [docs/configuration.md](../docs/configuration.md) and
`.env.example`. On the hosted side these live alongside the other
[#43](43-aws-hosting.md) hosted-only secrets.

---

## Implementation Phases

**Phase 0 — Plumbing & gate (no events yet).**
Add config fields; extend `/auth/status` with the `analytics` block; add
`posthog-js`; create `src/analytics/posthog.ts`; CSP allowlist gated on key
presence. *Acceptance:* with no key, no PostHog network calls and no global;
with a key, `posthog.init` runs once and a manual test event reaches the
project.

**Phase 1 — Pageviews & identity.**
Route-change `$pageview` in `App.tsx`; `identify` on auth resolve; `reset` on
logout. *Acceptance:* navigations and a logged-in person show up in PostHog;
self-host still silent.

**Phase 2 — Core product events.**
Wire the [taxonomy](#event-taxonomy): play/pause/complete (PlayerContext +
`ended`), search, entity, digest, briefing, import, follow, inbox actions.

**Phase 3 — Insights (PostHog-side, no code).**
Build the dashboards that make this worth doing: add-podcast → first-play
funnel; week-1 retention; feature-usage breakdown to spot dead features. This is
configuration in the PostHog UI, owned by the operator.

**Later / optional.**
Server-side `posthog-python` for events with no client moment (signup
completion, pipeline outcomes). Web-first; explicitly deferred.

---

## Testing

- The gate is the key, so unit/component tests run with analytics **off** by
  default — `track()` is a no-op and needs no mocking. Where a test asserts an
  event fires, mock the `analytics` module.
- An e2e (Playwright) check: with no key, assert **zero** requests to the
  PostHog host; with a stubbed key, assert init + a `$pageview` request.
- Keep the no-PII rule honest with a lightweight test that event-prop builders
  emit only IDs/enums/counts (no query text, no titles).

---

## Open Questions

1. **`/auth/status` vs dedicated `/api/config`.** Overloading auth is the
   fewest moving parts; a separate public endpoint is cleaner separation.
   Recommendation: start on `/auth/status`, split later if it grows.
2. **US vs EU PostHog residency.** Pick before launch; trivial to set via
   `POSTHOG_HOST`, annoying to migrate after data accumulates.
3. **Email as a person property.** Off in v1 for PII minimization; revisit if
   operator debugging needs to tie a session to a known person.
4. **Honor Do Not Track?** Cheap to add; decide whether to respect it given the
   audience is the operator's own opted-in hosted users.
5. **Cookie vs cookieless persistence.** PostHog cookies interact with the
   consent/privacy story; `persistence: 'memory'` avoids a cookie banner at the
   cost of cross-session identity. Decide alongside the privacy note.

---

## Non-Goals (restated)

No CLI analytics. No user-facing analytics. No autocapture or session replay in
v1. No change to structlog observability. No licensing/anti-rehosting work.
