# src

## Overview

Source root of the thestill React 19 + TypeScript single-page app. `main.tsx` builds the provider tree (StrictMode → ErrorBoundary → QueryClientProvider, holding `ListeningQuerySync` and BrowserRouter → AuthProvider → ToastProvider → App). `App.tsx` routes the signed-in app under `PlayerProvider` with the spec #52 background-location reader overlay; a signed-out visitor in multi-user mode gets the lazily loaded spec #94 public site (`PublicApp`). Spec #105 server-written public pages are not hydrated: `main.tsx` seeds the query cache from the page's data block, preloads its chunks, then `createRoot(...).render` replaces the server HTML.

## Local Interface & Invariants

- `App` (default) — signed-in route tree: `/login`, `/oauth/consent`, `/podcasts/:podcastSlug/episodes/:episodeSlug/edit` (transcript editor, outside the layout), `/terms`, `/privacy`, `/claude` (help page, no sign-in needed), and under `Layout`: `status`, `podcasts`, `top`, `podcasts/:podcastSlug`, `podcasts/:podcastSlug/episodes/:episodeSlug`, `episodes`, `inbox`, `briefings`, `briefings/:briefingId`, `failed`, `queue`, `analytics`, `admin/users`, `admin/users/:userId`, `admin/settings`, `admin/audit`, `settings`, `search`, `entities/:entityType/:idSlug` [src/App.tsx:App]
- `PublicApp` (default) — signed-out routes `/`, `/top`, `/podcasts/:podcastSlug`, `/podcasts/:podcastSlug/episodes/:episodeSlug`, `/entities/:entityType/:slug`, `/terms`, `/privacy`, `/claude`, `/login`; only `/inbox`, `/briefings/*`, `/search`, `/settings`, `/episodes`, `/status`, `/queue`, `/failed`, `/analytics`, `/podcasts` and `/oauth/consent` redirect to `/login?next=<path+search+hash>`; every other path, including `/admin/*` and the editor route, renders `NotFound` [src/PublicApp.tsx:PublicApp]
- `PublicApp` (preloadable), `preloadPublicSite` [src/publicEntry.ts:preloadPublicSite]; `readBootstrap`, `seedBootstrap`, `BOOTSTRAP_ID` (`thestill-bootstrap`), `BOOTSTRAP_VERSION` (1), `PrerenderedPage`, `PublicBootstrap` [src/publicBootstrap.ts:seedBootstrap]; `createQueryClient` [src/queryClient.ts:createQueryClient]; ambient `hls.js/light` module declaration [src/vite-env.d.ts]
- Invariant: `status`, `episodes`, `failed`, `queue`, `analytics` and `admin/*` routes are wrapped in `AdminRoute`; the layout, the editor, `/oauth/consent` and the overlay pass in `ProtectedRoute` [src/App.tsx:App]
- Invariant: the page `<Routes>` render `backgroundLocation ?? location`; while a background location exists the episode renders in `EpisodeReaderOverlay` above the still-mounted page, and `FloatingVideoTile` is not mounted [src/App.tsx:App]
- Invariant: `PlayerProvider` wraps both route passes so playback survives overlay open/close [src/App.tsx:App]
- Invariant: `AnalyticsBridge` and `ScrollRestoration` sit outside the page `<Routes>` (beneath a `<Routes location>` every navigation reads as POP) [src/App.tsx:ScrollRestoration]
- Invariant: `App` calls `useDriftSession()` on every render pass, so the spec #116 `drift.align` permission switches crossorigin loading and the service worker for the whole app, signed in or not [src/App.tsx:App]
- Invariant: the public site renders only when `isAnonymous`, which is false while auth loads, so a signed-in deep link never flashes the public layout [src/App.tsx:App]
- Invariant: no default `refetchInterval` (pinned by `queryClient.test.ts`); defaults are `staleTime: 60_000`, `retry: 1`; live hooks declare their own intervals [src/queryClient.ts:createQueryClient]
- Invariant: a bootstrap block with a different version, unknown page or non-array queries is ignored; only entries naming a `publicKeys` factory are seeded [src/publicBootstrap.ts:readBootstrap]
- Invariant: the Inter font is self-hosted to satisfy the `style-src 'self'` CSP; `styles/public.css` is in the entry stylesheet so server-written pages are styled before JS [src/main.tsx]

## Subdirectories & System Boundaries

- analytics: [./analytics/EGM.md](./analytics/EGM.md) - spec #95 usage events (lazy posthog-js via `/ingest`, allowlist sanitizer) and first-party public page views
- api: [./api/EGM.md](./api/EGM.md) - fetch clients for `/api/*`, `/api/admin`, `/api/me/listening`, `/api/public`, transcript edits; shared API types; `followPodcast` queue
- components: [./components/EGM.md](./components/EGM.md) - shell, route guards, episode reader, transcript/summary viewers, player surfaces, inbox/briefing/follow/import controls, operator pipeline UI, design system
- constants: [./constants/EGM.md](./constants/EGM.md) - navigation lists, z-index ladder, pipeline stage metadata, MCP scope copy
- contexts: [./contexts/EGM.md](./contexts/EGM.md) - `AuthProvider` and `PlayerProvider` with playback engines, the spec #116 timeline map and listening persistence
- drift: [./drift/EGM.md](./drift/EGM.md) - spec #116 drift alignment: sync-track parsing, landmark aligner, service-worker check of the element's copy, timeline application and outcome reports
- hooks: [./hooks/EGM.md](./hooks/EGM.md) - React Query hooks and navigation / scroll / deep-link / UI hooks
- pages: [./pages/EGM.md](./pages/EGM.md) - route screens, including the admin console and the public site
- utils: [./utils/EGM.md](./utils/EGM.md) - formatting, binary searches, safe redirects, sanitization, rate / MediaSession / chunk-reload / element audio URL helpers
- workers: [./workers/EGM.md](./workers/EGM.md) - Web Worker entry that runs a drift alignment off the main thread

## Global Invariants

- Every list page keeps filters / sort / page in the URL; window scroll goes to the top on PUSH and is restored on POP [src/hooks/useScrollRestoration.ts:useScrollRestoration]
- Entry points gate on server-provided flags: `isAdmin` for operator routes, `can('transcript.edit')` for the editor, `isMultiUser` for account pages [src/contexts/AuthContext.tsx:can]
- Exactly one playback engine is active over one stable `<video>` element; the YouTube iframe only plays while visibly presented [src/contexts/PlayerContext.tsx:PlayerProvider]
- Analytics never stores anything in the browser and never sends real URLs [src/analytics/client.ts:startAnalytics]

## Global Side Effects

- HTTP to the FastAPI backend under `/api` (cookies included) and `EventSource` progress streams [src/api/client.ts:API_BASE]
- `localStorage` / `sessionStorage` keys prefixed `thestill:` plus `reading-position-*` and `thestill.pendingFollow` [src/utils/playbackRate.ts:PLAYER_RATE_STORAGE_KEY]
- posthog-js via the same-origin `/ingest` proxy when the server issues a key [src/analytics/client.ts:startAnalytics]
- Injected YouTube IFrame API script and lazily loaded hls.js [src/contexts/playback-engine/youtube-iframe-api.ts:loadYouTubeIframeApi]
- Spec #116, with `drift.align` in a supported browser only: registers the `/sw.js` service worker, starts a drift Web Worker that downloads the episode audio a second time, and posts `drift-outcome` reports; without it, unregisters the worker and reloads once [src/drift/useDriftSession.ts:useDriftSession]
- `installChunkReloadHandler()` reloads once on a stale-deploy chunk error [src/main.tsx]

## System Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
