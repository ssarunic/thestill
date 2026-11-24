# components/public

## Overview

The signed-out shell and shared UI kit of the spec #94 public pages (landing, show, episode, entity, top chart, legal). Public pages render inside `PublicLayout`; their sign-in affordances follow spec #102 (one `/login` URL with `next` and `intent` in the fragment).

## Interface

- `PublicLayout` (default) — sticky header (Top podcasts, Sign in), footer with the model disclaimer and legal links, `<Outlet/>`, scroll restoration [src/components/public/PublicLayout.tsx:PublicLayout]
- `useSignIn`, `returnPath` — sign-in link for an action and the return path after sign-in [src/components/public/helpers.ts:useSignIn]
- `quoteUrl`, `quoteShareUrl` (spec #114: `/q/<share_id>` when the quote has one, else `quoteUrl`), `groupByEpisode`, `typeLabel` — quote deep link, consecutive-episode grouping, entity type label [src/components/public/helpers.ts:quoteUrl]
- `PublicSummaryViewer` — preloadable lazy `SummaryViewer` [src/components/public/summaryViewer.ts:PublicSummaryViewer]
- `Button` (`variant`, `size` `md|sm`), `GoogleG`, `EntityDot`, `FilterChip`, `QuoteCard`, `SignInCard`, `Avatar`, `EpisodeRow`, `EntityGroups`, `TakingPart` ("In this episode" people with their Guest / Host / Recurring role), `FactList`, `ListenOn`, `ActivitySparkline`, `Skeleton`, `QuoteSkeleton`, `EpisodeGroupHead` — public UI kit [src/components/public/ui.tsx:QuoteCard]

## Invariants

- Sign-in buttons keep their action names and lead to the single `/login` URL; `next` (default: this page with its fragment) and `intent` ride in the fragment [src/components/public/helpers.ts:useSignIn]
- An episode opened from a quote (`#t=`) returns after sign-in as `?view=transcript&t=`, the signed-in reader's own form of that link [src/components/public/helpers.ts:returnPath]
- A quote's `t` goes in the fragment so crawlers see one URL per episode, not one per quoted minute [src/components/public/helpers.ts:quoteUrl]
- `EntityGroups` keeps the server's order and folds past eight behind "Show all"; `TakingPart` is never folded and renders nothing for an empty list [src/components/public/ui.tsx:EntityGroups]
- Non-component helpers live in `helpers.ts` so `ui.tsx` stays component-only for React fast refresh [src/components/public/helpers.ts]

## Side Effects

- Quote share hands out `quoteShareUrl` and calls `reportQuoteShared(method)` (a fire-and-forget `POST /api/public/quote-shared`; the public site has no analytics client): `navigator.share` on touch (`(hover: none)`) devices, else `navigator.clipboard.writeText` with a 1.5 s "copied" timer [src/components/public/ui.tsx:QuoteCard]
- Imports `styles/public.css` and restores scroll on Back via `useScrollRestoration` [src/components/public/PublicLayout.tsx:PublicLayout]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
