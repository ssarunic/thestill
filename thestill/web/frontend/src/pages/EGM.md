# pages

## Overview

Route-level screens of the signed-in app (routed by `src/App.tsx`): inbox, podcasts and top chart, episode page, briefings, search, entities, settings, sign-in and OAuth consent, the transcript editor, and the admin pipeline views (status dashboard, episodes, queue, failed tasks, analytics). Data comes from `src/hooks`; layout and reader pieces from `src/components`. Admin console pages live in `./admin`, the signed-out public site in `./public`.

## Interface

- `Inbox` (default) — spec #88 All / Unread / Saved views (`?view=`), search (`?q=`; behind an icon on phones, open whenever there is a query), Arriving soon strip (folded to its one-line count until opened), listening progress; rows open the reader overlay [src/pages/Inbox.tsx:Inbox]
- `Podcasts` (default) — followed podcasts, server-side `q` search, infinite scroll [src/pages/Podcasts.tsx:Podcasts]
- `PodcastDetail` (default) — show page with episodes, follow/unfollow, completes a pending follow from sign-in [src/pages/PodcastDetail.tsx:PodcastDetail]
- `TopPodcasts` (default) — chart with region/category/q filters; follow or resolve-then-open a show [src/pages/TopPodcasts.tsx:TopPodcasts]
- `EpisodeDetail` (default) — standalone episode page: breadcrumb plus the shared `EpisodeReader` [src/pages/EpisodeDetail.tsx:EpisodeDetail]
- `Briefings` (default), `BriefingDetail` (default) — past briefings list and one briefing (episode index, script fallback, listened) [src/pages/BriefingDetail.tsx:BriefingDetail]
- `SearchResults` (default) — All / Quotes (corpus, hybrid) and Entities (quick) tabs; quotes grouped by episode and played inline [src/pages/SearchResults.tsx:SearchResults]
- `Entities` (default) — entity page with Wikidata enrichment and recent mentions that play inline [src/pages/Entities.tsx:Entities]
- `Settings` (default) — region, briefing schedule, MCP connector, usage events [src/pages/Settings.tsx:Settings]
- `Login` (default) — the one sign-in page (spec #102) [src/pages/Login.tsx:Login]
- `OAuthConsent` (default) — spec #109 consent for an MCP client's OAuth request [src/pages/OAuthConsent.tsx:OAuthConsent]
- `TranscriptEditorPage` (default) — spec #110 editor screen outside the app layout [src/pages/TranscriptEditorPage.tsx:TranscriptEditorPage]
- Admin pipeline views: `Dashboard` (status) [src/pages/Dashboard.tsx:Dashboard], `Episodes` (corpus browser with bulk actions) [src/pages/Episodes.tsx:Episodes], `QueueViewer` [src/pages/QueueViewer.tsx:QueueViewer], `FailedTasks` (DLQ) [src/pages/FailedTasks.tsx:FailedTasks], `Analytics` [src/pages/Analytics.tsx:Analytics]
- Subdirectory [admin](./admin/EGM.md) — spec #104 admin console (users, user detail, settings, audit)
- Subdirectory [public](./public/EGM.md) — spec #94 signed-out public site

## Invariants

- List filters, search and tabs live in the URL so Back restores them and the scroll position (Inbox `?view=`/`?q=`, Podcasts `q`, TopPodcasts filters, FailedTasks branch tab, Episodes filters, Analytics range) [src/pages/TopPodcasts.tsx:TopPodcasts]
- The inbox is an immutable delivery log: views filter by row state ("All" hides dismissed); rows are never removed [src/pages/Inbox.tsx:VIEWS]
- An inbox row link carries the inbox location in navigation state so `App` renders the episode in the reader overlay; Cmd/middle-click opens the standalone page [src/pages/Inbox.tsx:Inbox]
- `EpisodeDetail` observes `useEpisode` with `live: false` so it never keeps polling after the reader settles [src/pages/EpisodeDetail.tsx:EpisodeDetail]
- The transcript editor requires `can('transcript.edit')`; without it the page redirects back and editor queries stay disabled. Below the `sm` breakpoint it also redirects back (`if (!isSmUp) return <Navigate to={backTo} replace />`) [src/pages/TranscriptEditorPage.tsx:TranscriptEditorPage]
- Login reads `next` / `intent` from the fragment (public links) or `?next=` (app redirect), validated by `safeNextPath`; `account_disabled` and `signups_closed` notices show the operator contact [src/pages/Login.tsx:Login]
- A Follow pressed on a public show or (spec #117) episode before sign-in is stored on `/login` (`rememberPendingFollow` for `followSlugFromNext(next)`) and completed once on the show page or the episode header's `EpisodeFollow` (`consumePendingFollow`), never read from the URL [src/pages/PodcastDetail.tsx:PodcastDetail]
- OAuth consent takes the request id from the fragment, uses same-origin API calls, and refuses `javascript:`, `data:`, `vbscript:`, `blob:`, `file:` redirects [src/pages/OAuthConsent.tsx:isNavigable]
- Analytics is admin-only and shows aggregates only, no per-user rows [src/pages/Analytics.tsx:Analytics]
- FailedTasks defaults to the `user` branch; the default is omitted from the URL [src/pages/FailedTasks.tsx:FailedTasks]
- QueueViewer treats naive backend ISO timestamps as UTC (appends `Z`) and always renders one lane per pipeline stage [src/pages/QueueViewer.tsx:QueueViewer]
- Search quotes and entity mentions play inline through the player rather than navigating away; seeks within the current episode pass `{ transcript: true }` (spec #116) [src/pages/SearchResults.tsx:SearchResults]
- Top-level pages (Inbox, Podcasts, TopPodcasts, Search, Settings, Status, Episodes, Queue, Failed, Analytics, admin) carry no visible title; each keeps an `sr-only` `<h1>` [src/pages/Inbox.tsx:Inbox]
- Settings region options must cover the same codes as `FLAG` in `utils/regions.ts` [src/pages/Settings.tsx:Settings]
- Briefing opened is reported only once the briefing has loaded; the text script is fetched only as a fallback [src/pages/BriefingDetail.tsx:BriefingDetail]

## Side Effects

- Network I/O through `src/hooks` / `src/api` (inbox, podcasts, search, briefings, DLQ, queue, analytics, transcript edits) [src/pages/Inbox.tsx:Inbox]
- `followPodcast()` and `POST /api/podcasts/resolve` from the top chart [src/pages/TopPodcasts.tsx:TopPodcasts]
- `POST /api/oauth/pending/{id}/approve|deny`, then `window.location.assign(redirect)` [src/pages/OAuthConsent.tsx:OAuthConsent]
- `sessionStorage` pending-follow write on `/login` [src/pages/Login.tsx:Login]
- `PATCH /api/auth/me` (region) via `updateRegion` [src/pages/Settings.tsx:Settings]
- Player `play()` calls from search and entity pages [src/pages/Entities.tsx:Entities]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
