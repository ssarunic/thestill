# pages/public

## Overview

The spec #94 signed-out public site: landing, top chart, show, episode, person/entity pages, Terms/Privacy and the shared not-found layout. Pages read through `hooks/usePublicApi.ts` (`/api/public/*`) and render inside `components/public/PublicLayout`; `registry.ts` exposes them as preloadable chunks for spec #105 server-rendered first loads.

## Interface

- `Landing` (default) — one real summary, "who said what" across shows, recent summaries [src/pages/public/Landing.tsx:Landing]
- `TopChart` (default) — Apple Podcasts chart for one country; rows open the show's page when it has one, else Apple Podcasts [src/pages/public/TopChart.tsx:TopChart]
- `Show` (default) — hosts, where to listen, summarised episodes; Follow asks for sign-in [src/pages/public/Show.tsx:Show]
- `Episode` (default) — gist and key takeaways, speakers and their best quotes, "In this episode" (`TakingPart`, people with a `role`) then "People mentioned" and other entity groups, a small Follow that asks for sign-in; transcript behind sign-in [src/pages/public/Episode.tsx:Episode]
- `EntityPage` (default) — person/entity: what they said (filterable by `#about=`), where heard, what others say [src/pages/public/EntityPage.tsx:EntityPage]
- `Legal` (default) — Terms / Privacy from `public/legal/*.md` with operator placeholders [src/pages/public/Legal.tsx:Legal]; `LegalPage` (default) — the same outside the public site [src/pages/public/LegalPage.tsx:LegalPage]
- `Help` (default), `HelpDoc` (`'claude'`) — spec #108 / #109 help page from `public/help/{doc}.md` with operator placeholders plus `connector_address` (this origin + `/mcp`); routed at `/claude` in the public site [src/pages/public/Help.tsx:Help]; `HelpPage` (default) — the same for signed-in users, routed at `/claude` in `App` [src/pages/public/HelpPage.tsx:HelpPage]
- `NotFound` (default), `ModuleError` — one layout for every missing / hidden / unsummarised page; per-module retry [src/pages/public/NotFound.tsx:NotFound]
- `Landing`, `TopChart`, `Show`, `Episode`, `EntityPage` (preloadable), `preloadPublicPage` — chunk registry keyed by `PrerenderedPage` [src/pages/public/registry.ts:preloadPublicPage]

## Invariants

- A `PublicNotFound` (404) renders `NotFound`; other module failures render `ModuleError` with retry and keep the rest of the page [src/pages/public/EntityPage.tsx:EntityPage]
- Signed out, the episode summary is only the gist and key takeaways; "Read the full summary" goes through `useSignIn('summary', …)` back to this episode [src/pages/public/Episode.tsx:Episode]
- `t` is the shared deep-link parameter (query or fragment), accepted only as 1–6 digits; legacy `q` still works [src/pages/public/Episode.tsx:Episode]
- Filter and tab changes keep the fragment so a linked quote stays pinned [src/pages/public/Episode.tsx:Episode]
- Show Follow uses `useSignIn('follow')` and returns to the show; the episode's Follow (spec #117) uses `useSignIn('follow', episodeHref)` and returns to the episode, where the signed-in header completes it [src/pages/public/Episode.tsx:Episode]
- A person's topic filter lives in the URL fragment: filter chips and person links write `#about=<slug>` (the canonical form, kept across sign-in); the page reads `about` from the query or the fragment (`deepLinkParam`) [src/pages/public/EntityPage.tsx:EntityPage]
- For a server-written page, `main.tsx` loads its chunks (`PRELOADS`) before the first render so React's first commit replaces the server HTML with the full page [src/pages/public/registry.ts:PRELOADS]

## Side Effects

- `GET /api/public/*` via `usePublicApi` hooks [src/pages/public/Episode.tsx:Episode]
- `fetch` of `/legal/{terms,privacy}.md` and `/legal/operator.json` (query key `['public', 'legal', doc]`, 1 h stale time) [src/pages/public/Legal.tsx:fetchLegal]
- `fetch` of `/help/{doc}.md` and `/legal/operator.json` (query key `['public', 'help', doc]`, 1 h stale time); a failed fetch renders `NotFound` [src/pages/public/Help.tsx:Help]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
