# Product Analytics

Thestill measures use in two layers ([spec #95](../specs/95-product-analytics.md)):

| Layer | Source | Where you read it |
|---|---|---|
| A. Product funnel | Product tables: signups, follows, reads, briefings | `/analytics` admin page, `thestill analytics` |
| B. Usage events | PostHog Cloud (EU) | PostHog |

Layer A needs no configuration: it works on any multi-user Postgres
install. This page is mostly about Layer B.

## What Layer B sends

Only these events exist. Server events are Pydantic models in
`thestill/services/analytics_sink.py` (unknown props are a validation
error); browser events pass through `sanitizeEvent` in
`thestill/web/frontend/src/analytics/sanitize.ts`, which drops anything
else.

| Event | Sent by | Props |
|---|---|---|
| `$pageview` | signed-in app, on route change | `path_kind` (the route pattern, e.g. `/podcasts/:podcastSlug`) |
| `$set` | signed-in app, once per load | person property `is_admin` only (for the internal-users filter) |
| `share` | signed-in app (episode share button, transcript timestamp link) | `kind`: `episode` or `moment` |
| `entity_open` | signed-in app, entity page | `entity_type` |
| `reader_tab` | signed-in app, episode reader: once per tab shown per episode, the landing tab included | `tab`: `summary` or `transcript` |
| `read_depth` | signed-in app, episode reader: once per tab and milestone per episode, on scroll, once the tab's content has loaded | `tab`, `percent`: 25, 50, 75 or 100 (the bottom of the viewport within 2% of the end) |
| `public_page_view` | server, from `POST /api/public/pageview` | `kind`, `referrer_host` |
| `login_started` | server, `/api/auth/google/login` | `from_kind` |
| `login_completed` | server, OAuth callback (not for opted-out readers) | `next_kind` |
| `login_cancelled` | server, OAuth callback, when Google returns no code | `reason`: `access_denied` (Cancel on Google's screen), `admin_policy_enforced`, `server_error`, `temporarily_unavailable`, `other`, `missing_code` |
| `login_failed` | server, OAuth callback, when Google signed the reader in but thestill did not | `reason`: `account_disabled`, `signups_closed`, `error` |
| `search` | server, `/api/search/corpus` (not in-episode expansion) | `result_count`, `mode` |
| `follow` | server, when a follow is made (`POST /api/podcasts/{slug}/follow`, or an add from Top podcasts / Add podcast that created a new follow); never which show | `source`: `episode_prompt` (the reader's first-follow prompt), `show_page`, `pending_follow` (Follow pressed signed out, finished after sign-in), `top_podcasts`, `add_podcast`, `other` |
| `unfollow` | server, `DELETE /api/podcasts/{slug}/follow` | none |
| `mcp_tool_call` | server, remote MCP | `tool`, `ok` |

Never sent: email, name, search text, page text, full URLs or query
strings. Signed-in events carry the internal user UUID and the person
property `is_admin`; anonymous events carry a visitor key that rotates at
midnight UTC (none under Global Privacy Control) and never create a person.

The sign-in events are anonymous and share one id per sign-in attempt,
an HMAC of the OAuth `state` (`login_flow_key`), so they form a funnel
without a cookie and without ever joining the account's person or the
visitor's public page views. Each attempt ends in at most one of
`login_completed`, `login_cancelled` or `login_failed`; a `login_started`
with none of them is a reader who closed the tab or went back. An outcome
is only recorded when the callback's `state` matches the browser's state
cookie. A callback with no attempt in that browser, or a stale one from an
older tab after a newer sign-in replaced the cookie, sends nothing and
leaves the current attempt open. Remote MCP calls carrying `Sec-GPC: 1` send
nothing.

Not built yet: `export` (no spec #91 export routes exist) and
`narration_play` (no narration audio player in the web app).

## How it stays private

- **No third-party script.** `posthog-js` is bundled and lazy-loaded only
  by the signed-in app when `/api/auth/status` returns an `analytics` key.
  Public pages and `/login` never load it; they report views to our own
  `POST /api/public/pageview`.
- **No CSP change.** The browser sends to `/ingest` on our own domain;
  Caddy forwards it to `eu.i.posthog.com` and strips `Cookie`,
  `Authorization`, `Referer` and `X-Forwarded-For`, so PostHog never sees
  the session, the page URL the browser would otherwise put in `Referer`,
  or the reader's IP ([Caddyfile](../deploy/aws-ec2/Caddyfile)).
- **No reader IP at PostHog.** Browser events arrive from this server's
  address (no `X-Forwarded-For`) and server events are sent by it, so the
  privacy policy's IP promise does not depend on any PostHog project
  setting.
- **Labelled.** Every event carries `app: "thestill"` (set by the server
  sink and by `sanitizeEvent`), so a project shared with another site can
  separate the two.
- **No cookie, no storage.** `persistence: 'memory'`, bootstrapped with the
  user id.
- **Opt-outs on the server.** `users.usage_analytics_enabled` (Settings →
  Usage events) and `Sec-GPC: 1` both mean no key for the browser and no
  server events for that user. They also stop the two facts Thestill keeps
  only to measure (spec #103): `user_episode_views` (first open) and
  `user_episode_progress.listened_at` (last playback). Turning the switch
  off deletes both for that user.
- **Public page counts stay on our server.** `public_page_daily_views`
  (spec #103) counts signed-out browser views per page per day, keyed by
  the page's object id, with no visitor id, IP, user agent or referrer.
  PostHog still gets only `kind` and `referrer_host`.
- **Off by default.** No key, `ANALYTICS_ENABLED=false` or single-user mode
  gives `NullSink`; the startup log says `analytics_configured sink=null`.

## Turning it on (production)

1. Pick a PostHog project in the **EU** region: a new one, or an existing
   one shared with another site (see below), and apply the settings below.
2. Add the project key to SSM:

   ```bash
   aws ssm put-parameter --name /thestill/prod/POSTHOG_PROJECT_KEY \
     --type SecureString --value 'phc_…'
   ```

3. Deploy. The startup log should show `analytics_configured sink=posthog`.
4. Sign in, open a few pages, and check PostHog's activity view for
   `$pageview` events with a `path_kind` and no query strings.

## PostHog project settings

These live outside the repo; check them before step 2.

thestill does not rely on project-wide feature toggles: its SDK turns off
autocapture, replay, surveys, heatmaps, web vitals, dead clicks, exception
capture, flags and extra script loads in code, and no reader's IP reaches
PostHog. So it can share a project with another site (for example
sarunic.com) without changing that site's settings, including its
cookieless setup: thestill never sends PostHog's cookieless id.

Required, shared project or not:

- **Region: EU.** Caddy, `POSTHOG_HOST`, the SDK's `ui_host` and the privacy
  policy all say EU. A US-region project needs those changed, or a separate
  EU project.
- **Filter out internal and test users:** add `is_admin = true`, applied to
  every insight by default. It only matches persons with that property.
- **Data retention:** the privacy policy quotes `analytics_retention` from
  `thestill/web/frontend/public/legal/operator.json`. In a shared project
  the retention is the project's; change `operator.json` to match it, not
  the other way round.
- **DPA:** accepted in organisation settings, after checking it covers UK
  GDPR transfers.
- **Billing limit:** set, remembering the free tier is shared by every site
  in the project.
- **Organisation members:** the operator only (PostHog shows each person's
  event stream by UUID).

Belt and braces, only if the project is thestill's alone: turn off the
toggles above at project level as well, set person profiles to identified
only, and turn on "Discard client IP data".

## Dashboard: "thestill: product"

Rebuild from this list if the project is ever recreated. In a shared
project, filter every insight on `app = thestill`.

| Insight | Definition |
|---|---|
| Public views by kind | Trends, `public_page_view`, breakdown `kind`, daily |
| Top referrers | Trends, `public_page_view`, breakdown `referrer_host`, total |
| Sign-in starts by page | Trends, `login_started`, breakdown `from_kind` |
| Sign-in completion | Funnel `login_started` → `login_completed`, by `from_kind` |
| Sign-in outcomes | SQL: group by `distinct_id` over `login_*` events; completed, cancelled (by `reason`), failed (by `reason`), or abandoned when only `login_started` is present |
| WAU / MAU | Trends, any event, unique users, weekly and monthly |
| Feature use | Trends, `search` + `share` + `entity_open`, weekly |
| MCP calls by tool | Trends, `mcp_tool_call`, breakdown `tool`, and `ok = false` share |

PostHog numbers cover readers who share usage. Signups, activation and
retention come from Layer A, which counts everyone.

## Troubleshooting

- **Nothing arrives from a headless browser.** posthog-js drops events
  from bots, including `navigator.webdriver` and a `HeadlessChrome` brand in
  `navigator.userAgentData`. The Playwright suite
  (`tests/usage-events.spec.ts`) masks both.
- **`analytics_capture_failed` warnings.** The server SDK could not queue an
  event; the request it described still succeeded.
