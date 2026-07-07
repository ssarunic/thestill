# Inbox Reader Overlay Specification

> **Status:** 📝 Draft
> **Created:** 2026-07-07
> **Updated:** 2026-07-07
> **Author:** Product & Engineering
> **Related:** [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md), [#22 floating-media-player](22-floating-media-player.md), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md)

---

## Executive Summary

Opening an episode from the Inbox currently performs a full route change to
`/podcasts/{podcast}/episodes/{episode}` — the user is teleported out of their
triage context into the Podcasts hierarchy (breadcrumb, sidebar highlight, and
back-affordance all say "Podcasts"), and the only way back to where they were
is the browser back button.

This spec makes the Inbox behave like an actual inbox: clicking a row opens the
episode in a **reader overlay** rendered *above* the still-mounted inbox list.
Closing the reader (Esc, scrim click, or an explicit `← Inbox` control) lands
the user exactly where they left off — same scroll position, same filters, no
refetch flash. The URL in the address bar is still the canonical episode URL,
so refresh, sharing, and deep links keep working unchanged.

**Mental model:** Gmail / Slack thread view. The list is the workspace; the
item opens *over* it; dismissing the item returns to the workspace.

**Key principle:** One canonical URL per episode. The overlay is a *rendering
mode* selected by navigation state, not a second address for the same content.
A dedicated `/inbox/{episode}` route was considered and rejected (see
[Alternatives](#alternatives-considered)).

---

## Table of Contents

1. [Motivation](#motivation)
2. [Product Requirements](#product-requirements)
3. [Architecture](#architecture)
4. [Component Changes](#component-changes)
5. [Interaction Design](#interaction-design)
6. [Interplay with Existing Features](#interplay-with-existing-features)
7. [Alternatives Considered](#alternatives-considered)
8. [Implementation Phases](#implementation-phases)
9. [Testing](#testing)
10. [Open Questions](#open-questions)
11. [Non-Goals](#non-goals)

---

## Motivation

Spec #29 made the Inbox the daily entry point (the root route redirects to
`/inbox`). Spec #29's read tracking (shipped 2026-07-07) marks a row read when
its episode page is viewed with a summary present. What remains is the
*navigation* mismatch:

- **Context loss.** The inbox is a triage surface — the user works through a
  list. Every click currently destroys the list (unmount → route change) and
  rebuilding it on return costs a refetch, scroll restoration, and a poll
  restart.
- **Disorientation.** The episode page's breadcrumb reads
  `Podcasts / {podcast} / {episode}` and the sidebar highlights **Podcasts**,
  even though the user never left their inbox mentally. There is no `← Inbox`
  affordance anywhere on the page.
- **Triage friction.** Read-and-return is the core inbox loop. Today it takes
  a browser-back plus a list re-render; it should be one keypress (Esc).

---

## Product Requirements

### User Stories

| As a | I want | So that |
|------|--------|---------|
| User | to open an inbox episode without leaving the inbox | I keep my place in the list while reading |
| User | to close the reader with Esc / scrim / `← Inbox` and land exactly where I was | triage feels like one continuous activity |
| User | the URL to still be the canonical episode link while reading | I can copy/share it and refresh without breakage |
| User | the row to show `read` the moment I return to the list | I can see my progress through the inbox |
| User (mobile) | the reader to open full-screen with a `← Inbox` back control | small screens don't waste space on a scrim |

### Core Behaviors

1. **Overlay open.** Clicking an inbox row pushes the canonical episode URL
   onto history with the inbox recorded as *background location*. The inbox
   page stays mounted underneath; the reader renders in an overlay above it.
2. **Overlay close.** Esc, scrim click, or the `← Inbox` button perform
   `history.back()` — restoring the inbox URL and its live component state
   (scroll offset, poll timers, query cache).
3. **Refresh / direct link degrade gracefully.** Reloading while the overlay
   is open (or opening a shared episode URL) loses the navigation state and
   renders the existing standalone `EpisodeDetail` page. This is correct: a
   fresh session has no inbox context to return to.
4. **All entry points except the inbox are unchanged.** Podcast pages, search
   results, briefing links, and related-episode links continue to navigate to
   the standalone page.
5. **Read marking is inherited, not reimplemented.** The overlay renders the
   same reader component, so `useMarkInboxReadOnView` (spec #29) fires exactly
   as it does on the standalone page, and its `['inbox']` invalidation updates
   the still-mounted list behind the overlay in place.

### Non-Goals (v1)

- Split-pane (list left / reader right) layout — see [Open Questions](#open-questions).
- Keyboard triage (j/k next/prev, `e` to dismiss) — Phase 2.
- Read/save/dismiss action buttons in the reader header — Phase 2 (this
  finally completes spec #29 Phase 3's "read/save/dismiss actions" item).
- Any backend or API change. This is a frontend-only spec.

---

## Architecture

### The background-location pattern

React Router's canonical modal-route recipe. The inbox row link carries the
current location in navigation state:

```tsx
// Inbox.tsx — InboxRow
<Link
  to={episodeHref}
  state={{ backgroundLocation: location }}   // location = /inbox at click time
>
```

[App.tsx](../thestill/web/frontend/src/App.tsx) splits rendering into a
background pass and an overlay pass:

```tsx
function App() {
  const location = useLocation()
  const background = (location.state as { backgroundLocation?: Location } | null)
    ?.backgroundLocation

  return (
    <>
      {/* Background: when an overlay is open, keep rendering the page the
          user came from (the inbox) at its own location. */}
      <Routes location={background || location}>
        {/* ...existing routes, unchanged, including the standalone
            podcasts/:podcastSlug/episodes/:episodeSlug route... */}
      </Routes>

      {/* Overlay: only mounted while navigation state carries a background. */}
      {background && (
        <Routes>
          <Route
            path="podcasts/:podcastSlug/episodes/:episodeSlug"
            element={<EpisodeReaderOverlay />}
          />
        </Routes>
      )}
    </>
  )
}
```

Consequences worth stating explicitly:

- **URL is canonical at all times.** The address bar shows
  `/podcasts/{pod}/episodes/{ep}` while the overlay is open. Copy/paste and
  refresh behave exactly like today.
- **The inbox never unmounts** while reading — its scroll position, its 5s
  processing poll, and its React Query cache all stay live.
- **History shape is natural.** Open = one pushed entry; close = one
  `history.back()`. Deep back-stacks (open → close → open another) behave the
  way browsers users expect.
- **Layout still wraps everything.** The overlay routes render inside the
  existing `<Layout>` tree, so `PlayerProvider` / `MiniPlayer`
  ([Layout.tsx](../thestill/web/frontend/src/components/Layout.tsx), spec #22)
  are untouched and playback continues across open/close.

### Sidebar highlight

While the overlay is open the *background* location is `/inbox`, so the
sidebar's active-state derivation must use the background location when one is
present (single helper in `Layout`; nav items themselves stay in
`constants/navigation.tsx` per the established convention). Result: **Inbox
stays highlighted while reading from the inbox** — which is the whole point.

---

## Component Changes

### 1. Extract `EpisodeReader` from `EpisodeDetail`

[EpisodeDetail.tsx](../thestill/web/frontend/src/pages/EpisodeDetail.tsx)
(~740 lines) is currently page-shaped: breadcrumb + header card + tabs +
entity rail + timeline. Split it:

| Piece | Stays in `EpisodeDetail` (page) | Moves to `EpisodeReader` (shared) |
|---|---|---|
| Breadcrumb (`Podcasts / … / …`) | ✅ | — |
| Header card (artwork, title, state pill, play button, failure banner, pipeline button) | — | ✅ |
| Summary / Transcript tabs, karaoke chip, drift banner | — | ✅ |
| Entity strip, rail, filter bar, density timeline (spec #28) | — | ✅ |
| Data hooks (`useEpisode`, `useEpisodeSummary`, `useEpisodeTranscript*`, `useEpisodeEntities`, `useRelatedEpisodes`, `useMarkInboxReadOnView`) | — | ✅ |

`EpisodeDetail` becomes breadcrumb + `<EpisodeReader …/>`. The new
`EpisodeReaderOverlay` becomes chrome (scrim, panel, close controls, focus
trap) + `<EpisodeReader …/>`. No logic is duplicated; the reader owns its own
data fetching keyed off route params in both modes.

### 2. `EpisodeReaderOverlay` chrome

- **Desktop (≥ lg):** right-aligned slide-over panel, `max-w-4xl`-ish, full
  height, own scroll container, dimmed scrim over the inbox. Wide enough that
  the reader's existing `lg:` two-column grid (content + entity rail) still
  engages.
- **Mobile (< lg):** full-screen sheet; the scrim is invisible/irrelevant;
  `← Inbox` in the sheet header is the primary exit.
- **A11y:** `role="dialog"` + `aria-modal="true"`, focus moved into the panel
  on open, focus trapped while open, focus restored to the originating row on
  close, body scroll locked behind the overlay.

### 3. Scroll-container awareness

Two existing hooks assume `window` is the scroll container:

- [useReadingPosition.ts](../thestill/web/frontend/src/hooks/useReadingPosition.ts)
  (persist/restore reading position) reads `window.scrollY` and calls
  `window.scrollTo`.
- Transcript auto-scroll/karaoke follow (spec #38) may share the assumption.

The overlay scrolls its own `div`. Both hooks gain an optional
`scrollContainerRef` parameter (default: window, preserving standalone-page
behavior). This is the only genuinely fiddly part of the build — budget for it.

---

## Interaction Design

| Trigger | Behavior |
|---|---|
| Click inbox row | Overlay opens; URL becomes canonical episode URL |
| Esc | `history.back()` → inbox restored |
| Scrim click (desktop) | Same as Esc |
| `← Inbox` button (panel header) | Same as Esc |
| Browser back | Same thing — it *is* the same mechanism |
| Refresh while open | Standalone episode page (state lost by design) |
| Cmd/middle-click inbox row | New tab → standalone page (no state transfers to new tabs; correct) |
| Click related episode inside overlay | v1: navigates within the overlay, *preserving* `backgroundLocation` (replace semantics — see Open Questions) |
| Click podcast title inside overlay | Standalone navigation to the podcast page (leaves inbox context deliberately) |

The row click continues to mark `unread → read` via the inherited
`useMarkInboxReadOnView`; because the inbox stays mounted and the mutation
invalidates `['inbox']`, the badge behind the overlay flips to `read` while
the user is still reading — visible the instant the overlay closes.

---

## Interplay with Existing Features

- **Read tracking (spec #29).** Unchanged; inherited via `EpisodeReader`. The
  summary-present gate keeps premature clicks on still-processing episodes
  from marking read — same as the standalone page.
- **Floating player (spec #22).** `PlayerProvider` sits above routes in
  `Layout`; opening/closing the overlay neither mounts nor unmounts it. The
  `MentionDensityTimeline` beside the MiniPlayer is gated on
  `player.isCurrent(episodeId)` and keeps working, though z-index layering vs
  the scrim needs a one-time check.
- **Entity UX (spec #28).** Entity strip/rail/filters render inside the
  reader in both modes. Entity links (`/entities/…`) navigate standalone —
  the user is deliberately leaving the inbox.
- **Import flow (spec #31).** Imported episodes land in the inbox and open in
  the overlay like any row; the processing pill logic in `InboxRow` is
  untouched.

---

## Alternatives Considered

1. **Context-aware breadcrumb only** (pass `state={{ from: 'inbox' }}`, swap
   the breadcrumb for `← Inbox`). Cheapest fix; solves the disorientation but
   not the context loss — the list still unmounts, scroll/poll/query state
   still resets. Rejected as the end-state; it is, however, a fine
   intermediate ship if Phase 1 needs to be split (the `Link state` plumbing
   is a subset of this spec's).
2. **Inbox-scoped route** (`/inbox/{episode}`). Survives refresh with inbox
   chrome, but mints a second URL for every episode — sharing, canonicalness,
   and "which URL do related-links use?" all get worse. Rejected.
3. **Email-style persistent split pane.** Best-in-class triage ergonomics,
   but a full layout redesign (nav, mobile, reading width) and the reader
   content (long transcripts, two-column entity rail) wants more width than a
   pane allows. Deferred — the overlay does not foreclose it.

---

## Implementation Phases

### Phase 1 — Overlay core

- [ ] `EpisodeReader` extraction from `EpisodeDetail` (no behavior change to
      the standalone page; vitest snapshot of both modes).
- [ ] Background-location split in `App.tsx`; `state={{ backgroundLocation }}`
      on `InboxRow`'s link.
- [ ] `EpisodeReaderOverlay` chrome: scrim, panel, Esc/scrim/`← Inbox` close,
      focus trap + restore, body scroll lock, mobile full-screen variant.
- [ ] Sidebar active-state derives from background location when present.
- [ ] `useReadingPosition` (+ transcript follow if affected) accept a scroll
      container ref.

### Phase 2 — Triage affordances

- [ ] Read/save/dismiss buttons in the overlay header (wires up the remaining
      `POST /api/inbox/{id}/state` UI from spec #29 Phase 3).
- [ ] Next/previous episode within the inbox ordering (buttons + `j`/`k`),
      replace-navigation inside the overlay.
- [ ] Unread-count badge on the sidebar Inbox item (backend endpoint exists;
      `getInboxUnreadCount` client fn exists; hook + badge missing).

### Phase 3 (optional) — Polish

- [ ] Open/close transition (slide + fade, `prefers-reduced-motion` aware).
- [ ] Preserve overlay scroll position across related-episode navigation
      within one overlay session.

---

## Testing

- **Vitest:** background/overlay route split (open renders both trees; close
  restores; refresh simulation renders standalone); `EpisodeReader` renders
  identically in page and overlay mode; sidebar highlight derivation.
- **Playwright (manual or scripted smoke):** click row → overlay opens, URL
  canonical, inbox visible behind scrim → Esc → inbox scroll preserved, row
  badge `read`, no refetch of the list route; cmd-click → standalone page in
  new tab; refresh while open → standalone page.
- Existing `Inbox.test.tsx` progress-badge tests and
  `useMarkInboxReadOnView.test.tsx` must pass unchanged.

---

## Open Questions

1. **Related-episode clicks inside the overlay** — stay in the overlay
   (proposed: yes, with `replace` so back still closes in one step) or jump to
   the standalone page? Staying matches the "one continuous activity" model;
   replacing avoids Esc having to unwind N episodes.
2. **Should Esc-close be suppressed while audio is playing from the overlay's
   episode?** Proposed: no — playback is global (spec #22) and survives close;
   nothing is lost.
3. **Overlay width vs entity rail** — if `max-w-4xl` proves too narrow for the
   two-column `lg` grid, either widen the panel or collapse the rail into the
   strip in overlay mode. Decide with real content during Phase 1.
4. **Does the split pane (alternative 3) ever happen?** If yes, `EpisodeReader`
   is the component both futures share — which is an argument for doing the
   extraction carefully rather than minimally.

---

## Non-Goals

- No backend, API, or schema changes of any kind.
- No change to how non-inbox surfaces navigate to episodes.
- No offline reading, no prefetching of adjacent episodes (evaluate with
  Phase 2's next/prev if wanted).
- No change to canonical URLs or addition of alternate episode URLs.

---

## Decision Log

| Date | Decision |
|------|----------|
| 2026-07-07 | Spec created. Overlay-over-inbox chosen over breadcrumb-only fix (doesn't solve context loss) and `/inbox/{episode}` route (duplicate URLs). Canonical-URL + background-location pattern selected. |
