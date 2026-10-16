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
| `share` | signed-in app (episode share button, transcript timestamp link) | `kind`: `episode` or `moment` |
| `entity_open` | signed-in app, entity page | `entity_type` |
| `public_page_view` | server, from `POST /api/public/pageview` | `kind`, `referrer_host` |
| `login_started` | server, `/api/auth/google/login` | `from_kind` |
| `login_completed` | server, OAuth callback (not for opted-out readers) | `next_kind` |
| `search` | server, `/api/search/corpus` (not in-episode expansion) | `result_count`, `mode` |
| `mcp_tool_call` | server, remote MCP | `tool`, `ok` |

Never sent: email, name, search text, page text, full URLs or query
strings. Signed-in events carry the internal user UUID and the person
property `is_admin`; anonymous events carry a visitor key that rotates at
midnight UTC (none under Global Privacy Control) and never create a person.

The two sign-in events are anonymous and share one id per sign-in attempt,
an HMAC of the OAuth `state` (`login_flow_key`), so they form a funnel
without a cookie and without ever joining the account's person or the
visitor's public page views. Remote MCP calls carrying `Sec-GPC: 1` send
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
  `Authorization` and `Referer`, so PostHog never sees the session or the
  page URL the browser would otherwise put in `Referer`
  ([Caddyfile](../deploy/aws-ec2/Caddyfile)).
- **No cookie, no storage.** `persistence: 'memory'`, bootstrapped with the
  user id.
- **Opt-outs on the server.** `users.usage_analytics_enabled` (Settings →
  Usage events) and `Sec-GPC: 1` both mean no key for the browser and no
  server events for that user.
- **Off by default.** No key, `ANALYTICS_ENABLED=false` or single-user mode
  gives `NullSink`; the startup log says `analytics_configured sink=null`.

## Turning it on (production)

1. In PostHog, create a project in the **EU** region and apply the settings
   below.
2. Add the project key to SSM:

   ```bash
   aws ssm put-parameter --name /thestill/prod/POSTHOG_PROJECT_KEY \
     --type SecureString --value 'phc_…'
   ```

3. Deploy. The startup log should show `analytics_configured sink=posthog`.
4. Sign in, open a few pages, and check PostHog's activity view for
   `$pageview` events with a `path_kind` and no query strings.

## PostHog project settings

These live outside the repo; apply them before step 2.

- **Discard client IP data:** on.
- **Autocapture, heatmaps, web vitals, dead clicks, exception capture,
  session replay, surveys:** all off (the SDK disables them too, so an SDK
  upgrade that changes a default still sends nothing).
- **Person profiles:** identified only.
- **Filter out internal and test users:** `is_admin = true`, applied to
  every insight by default.
- **Billing limit:** at the free tier.
- **Organisation members:** the operator only (PostHog shows each person's
  event stream by UUID).
- **DPA:** accepted in organisation settings, after checking it covers UK
  GDPR transfers.
- **Data retention:** must match `analytics_retention` in
  `thestill/web/frontend/public/legal/operator.json` (the privacy policy
  quotes it).

## Dashboard: "thestill: product"

Rebuild from this list if the project is ever recreated.

| Insight | Definition |
|---|---|
| Public views by kind | Trends, `public_page_view`, breakdown `kind`, daily |
| Top referrers | Trends, `public_page_view`, breakdown `referrer_host`, total |
| Sign-in starts by page | Trends, `login_started`, breakdown `from_kind` |
| Sign-in completion | Funnel `login_started` → `login_completed`, by `from_kind` |
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
