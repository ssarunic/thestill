# analytics

## Overview

Browser side of spec #95 usage events. The signed-in app lazily loads posthog-js only when the server hands out a key, routes events through the same-origin `/ingest` proxy, and strips every event through an allowlist sanitizer. Public pages count views with a first-party POST instead of any third-party code.

## Interface

- `startAnalytics`, `track`, `stopAnalytics`, `resetAnalytics`, `gpcEnabled` — SDK lifecycle and event capture [src/analytics/client.ts:startAnalytics]
- `AnalyticsBridge` (default) — starts/stops events from `useAuth()` and reports `$pageview` with the route pattern [src/analytics/AnalyticsBridge.tsx:AnalyticsBridge]
- `PublicPageViews` (default) — posts each public route change to the server [src/analytics/PublicPageViews.tsx:PublicPageViews]
- `APP_ROUTE_PATTERNS` (includes `/claude`), `routePattern` — map a pathname to its app route pattern, else `/other` [src/analytics/routes.ts:routePattern]
- `sanitizeEvent` — posthog `before_send`: allowlisted events and props only [src/analytics/sanitize.ts:sanitizeEvent]

## Invariants

- posthog-js is imported lazily, only by the signed-in app and only with a server key; public pages, `/login` and opted-out readers never load it [src/analytics/client.ts:startAnalytics]
- Global Privacy Control: the SDK is never loaded [src/analytics/client.ts:gpcEnabled]
- `persistence: 'memory'`, no autocapture, replay, surveys, flags, heatmaps or external script loads; events go to `/ingest` [src/analytics/client.ts:startAnalytics]
- Stopping is an in-memory flag, never posthog-js opt-out (which writes to `localStorage` and mutes later sessions) [src/analytics/client.ts:stopAnalytics]
- Only events in `EVENT_PROPS` (`$set`, `$pageview`, `share`, `entity_open`, `reader_tab`, `read_depth`, and spec #116 `ad_break_expanded` with `state`) leave the browser, each with its allowlisted props; unknown SDK props are dropped [src/analytics/sanitize.ts:EVENT_PROPS]
- The only person property is `is_admin`, only on `$set` [src/analytics/sanitize.ts:sanitizeEvent]
- Page views report the route pattern, never the real URL; URL-valued SDK props are rewritten to a pattern or bare host [src/analytics/routes.ts:routePattern]
- `APP_ROUTE_PATTERNS` must stay in step with `App.tsx` routes (checked by `routes.test.ts`) [src/analytics/routes.ts:APP_ROUTE_PATTERNS]
- `AnalyticsBridge` is mounted outside the page `<Routes>` so an episode opened in the reader overlay counts as an episode view [src/analytics/AnalyticsBridge.tsx:AnalyticsBridge]
- `/login` is never counted as a public page view [src/analytics/PublicPageViews.tsx:PublicPageViews]

## Side Effects

- Dynamic `import('posthog-js')`, `client.init(...)`, `setPersonProperties({ is_admin })`, `capture(...)`, `reset()` on sign-out [src/analytics/client.ts:startAnalytics]
- `POST /api/public/pageview` with `{ path, referrer }` (`keepalive`; referrer only on the first view) [src/analytics/PublicPageViews.tsx:PublicPageViews]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
