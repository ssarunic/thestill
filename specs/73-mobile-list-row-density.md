# Mobile List Row Density — `ListRow` Primitive

**Status**: 🚧 Phases 1–2 implemented on `claude/mobile-list-layout-design-dm3ypg` (2026-09-04); Phase 3 open
**Created**: 2026-09-04
**Updated**: 2026-09-04
**Priority**: Medium (Top podcasts is unreadable on phones; the same row anatomy drifts across five lists)

> **Related:** [#27 add-podcast-search-discoverability](27-add-podcast-search-discoverability.md) (defines the Top podcasts row: rank, name, artist, category, action), [#09 single-user-web-ui](09-single-user-web-ui.md) §Visual Design (8 px grid, 8 px card radius, Inter scale), [#29 per-user-inbox-fanout](29-per-user-inbox-fanout.md) (Inbox row, the closest existing phone row), [#71 player-shell-layer](71-player-shell-layer.md) (mini player height budget), [#13 multi-user-shared-podcasts](13-multi-user-shared-podcasts.md) (names `Button.tsx` as the shared primitive)

## Executive Summary

On a 393 px-wide phone the Top podcasts row gives the podcast title 57 px
(rows already followed) or 90 px (rows not yet followed) out of 361 px of
content width. Every title truncates after two words. The cause is not one
class but the row's anatomy: a 40 px rank column set in `text-xl`, three
16 px gaps, an "Apple" attribution link, and a text pill whose width changes
with state, all fixed-width and all competing with the only flexible column.

The row is one of eight hand-rolled media-object rows in the frontend, each
with its own padding, thumbnail size, title scale and truncation policy. The
fix therefore lands as a design-system primitive, `ListRow` + `ListGroup`,
adopted first by Top podcasts and then by Inbox, Briefings and the activity
feed, rather than as a page patch.

On phones the group runs **full-bleed**: no card border, no radius, rows
padded to the page's own 16 px so the content edge lines up with the heading.
Together with a two-character rank column this removes the double frame
(screen edge → page padding → card border → row padding) that made the left
of the row read as empty.

Result on the same phone: title width 216 px (3.8×), 10 rows per screen
instead of 8.7, 44 px touch targets, one row shape across the app, and the
blank space left of a single-digit rank halved from 48 px to 24 px.

## 1. Measured problem

Content width at 393 px is 361 px (layout `p-4`). Inside the current row
(`TopPodcasts.tsx`, classes `px-4 py-3 gap-4`, rank `w-10 text-xl`,
artwork `w-12`, trailing `Apple` link + `px-3 py-1.5 text-xs` pill):

| Element | Following row | Follow row |
|---|---:|---:|
| Card border + padding | 34 | 34 |
| Rank column + gap | 56 | 56 |
| Artwork + gap | 64 | 64 |
| Gap before trailing | 16 | 16 |
| Trailing (Apple + pill) | 134 | 101 |
| **Title + artist (flexible)** | **57** | **90** |

Findings, in order of cost:

1. The trailing group is the widest element in the row, and its width varies
   with state, so the title column jumps between rows.
2. "Apple" is attribution, not an action: every row says it. It is also an
   `<a>` nested inside an `<li role="link">`, which screen readers announce
   as a link inside a link.
3. The rank is the largest text on screen (`text-xl` vs a `text-base`
   title) although list order already communicates it. The chart caps at
   500 entries; three digits at `text-sm` fit in 28 px.
4. Three 16 px gaps and 16 px card padding are desktop density on a 361 px
   canvas. The row has no `sm:` breakpoints.
5. The title uses `truncate`; Inbox and Episodes already use `line-clamp-2`.
6. The Follow pill is ~30 px tall, below the 44 px minimum `Button.tsx`
   enforces.

## 2. Survey of existing rows

| List | Row padding | Thumbnail | Title | Phone breakpoints | Trailing on phone |
|---|---|---|---|---|---|
| Top podcasts | `px-4 py-3 gap-4` | 48 `rounded-md` | base · 500 · truncate | none | Apple link + text pill |
| Inbox | `p-3 sm:p-4 gap-3` | 44→48 `rounded` | `text-[15px]` · clamp-2 | yes | timestamp |
| Episodes | `p-3 sm:p-4 gap-3` | 40 `rounded-md` | sm→base · clamp-2 | yes | 1–3 badges |
| Briefings | `p-4 gap-4` | none | sm · 600 · no clamp | none | badge + "Read →" |
| Activity feed | `p-4 gap-4` | 40 `rounded-lg` | base · truncate | none | timestamp |
| Search entities | `px-4 py-3 gap-3` | 40 `rounded-full` | 600 · truncate | grid only | "View →" |
| Add podcast modal | `px-3 py-2 gap-3` | none | sm · truncate | none | text pill |
| ⌘K command bar | `px-4 py-2` | 32 `rounded` | sm · truncate | n/a | none |

Observations that shape the design:

- Inbox is the reference phone row (15 px title, two-line clamp, 44 px
  thumbnail), not Top podcasts.
- Seven thumbnail size/radius combinations exist for the same object.
- `Button.tsx` is used at 5 call sites; ~35 buttons are hand-rolled in four
  "primary" colours: `primary-900` (navy, 8 files), `indigo-600` (Button's
  own default plus two verbatim copies in `RefreshButton` and
  `BulkActionsBar`), `blue-600` (FailedTasks), `primary-600`
  (BriefingDetail). Indigo is not a token in `tailwind.config.js`; navy is.
- No spec or guideline documents touch targets or a truncate-vs-clamp
  policy; the only 44 px rule in the repo is inside `Button.tsx`.

## 3. Options considered

| | Title width | Rows / screen | Touch target | Files | Fixes other lists |
|---|---:|---:|---|---:|---|
| Current | 57–90 px | 8.7 | 30 px | 0 | – |
| A · Trim in place | 171 px | 8.7 | 44 px | 1 | no |
| B · Stacked actions | 227 px | 6.0 | 30 px | 1 | no |
| **C · ListRow + full-bleed group** | **216 px** | **10.4** | 44 px | 8 + 3 new | Inbox, Briefings, Activity, Search |

Rows per screen assume 716 px of list height (852 px viewport less 56 px
header and 80 px mini player).

**A — Trim in place.** Edit only `TopPodcasts.tsx`: rank `w-7 text-sm`,
`gap-3`, Apple link `hidden sm:inline-flex`, Follow as a 44 px icon-only
button below `sm`. One file; triples title width; leaves Briefings, the
activity feed and search rows with the same defect and adds one more
hand-rolled row.

**B — Stacked actions.** Trailing group moves to a second line under the
text. Widest title, text labels survive, but each row grows to ~118 px so
six rows fit where ten could. A ranked chart is scanned vertically; trading
a third of the rows for label text is the wrong currency. The Follow
control's position also drifts with title length.

**C — `ListRow` primitive, full-bleed `ListGroup`.** Recommended and
implemented. A and C deliver similar title width; the difference is whether
the fix is a value the rest of the app inherits. C also brings Top podcasts
onto the typography Inbox already uses, so the two most-visited lists look
like one product.

### 3.1 Left-edge refinement (folded in after review)

The first cut of C kept a bordered card on phones and a 28 px rank column
sized for three digits. Left of a single-digit rank that stacked 16 px page
padding + 1 px border + 12 px row padding + 19 px of empty column = 48 px
before the glyph. Two changes, both adopted:

| Change | Effect |
|---|---|
| Rank column `min-w-[2ch]`, right-aligned, tabular | Sized for the 99 ranks above the fold; widens itself once at rank 100 instead of jittering per row. −11 px. |
| Group full-bleed below `sm` (`-mx-4`, `border-y` only, rows `px-4`) | Kills the double frame; row content aligns with the page heading, like the mobile header and mini player which are already full-bleed. +34 px to the title. |

Rejected: left-aligning the rank (artwork column then shifts between one-
and two-digit ranks) and overlaying the rank on the artwork (needs a scrim,
fights busy covers, loses the vertical scan).

## 4. Row specification (Option C)

| Slot | Classes / rule |
|---|---|
| Group (`ListGroup`) | `bg-white divide-y divide-gray-100 border-y border-gray-200 -mx-4 sm:mx-0 sm:border sm:rounded-lg sm:overflow-hidden`. Full-bleed below `sm`; bordered, rounded card from `sm`. Replaces `space-y-2` / `space-y-3` plus per-row borders. `as="ol"` for ranked lists. |
| Row (`ListRow`) | `relative flex gap-3 px-4 py-2.5 sm:py-3 min-h-[64px] hover:bg-gray-50`, `items-center` or `items-start`. The **title** carries the `<Link>` (or a `<button>` for resolve-then-navigate rows) and stretches it over the row with an `after:absolute after:inset-0` overlay, so the row is one real anchor with no nested interactive content; the trailing slot sits above the overlay on `relative z-10`. Focus ring is drawn on the overlay. |
| Leading · rank | `min-w-[2ch] shrink-0 text-right text-sm font-semibold tabular-nums text-gray-400`. Omitted for unranked lists. Accepts a node so a row can swap in a spinner while resolving. Never larger than the title. |
| Leading · artwork (`ListRowArtwork`) | `w-12 h-12 rounded-md object-cover shrink-0` for podcast, inbox and activity rows; `w-10 h-10` for episode rows within a podcast; `w-8 h-8` in ⌘K. Always `rounded-md`; the generic-podcast placeholder is built in. |
| Body · title | `text-row sm:text-base leading-snug line-clamp-2` + `titleClassName` (default `font-medium text-gray-900`; Inbox passes its unread/read weight). Never `truncate` on a primary title. |
| Body · overline / subtitle / footer | `overline` is a small line above the title (Inbox: podcast name + timestamp; Activity: action badge + timestamp). `subtitle` is `text-xs sm:text-sm text-gray-500 truncate mt-0.5` — artist first, then `· category`, so the category is what truncates. `footer` holds pills/badges under the subtitle. |
| Trailing (phone) | At most one element, 44 × 44 px: icon Button, status badge or timestamp. Text labels return at `sm` via `iconOnlyMobile`, which now renders the label `sr-only sm:not-sr-only` (was `hidden`, which left icon-only buttons with no accessible name). |
| Follow | Shared `FollowButton` (extracted from `AddPodcastModal`) on `Button`: idle = `tonal` + plus icon; pending = spinner; done = green tint + check, `aria-label="Following"`; error = `danger` "Retry". Label text stays in the DOM as `sr-only` below `sm` so name-based tests keep passing. |
| Apple link | Hidden below `sm`; ghost icon button with `aria-label="Open in Apple Podcasts"` at `sm`+. Follow-up: surface Apple/YouTube links on the podcast detail page (requires storing `apple_url` on the local podcast row). |

## 5. Design-system changes and ripple

### 5.1 `Button.tsx`: `tonal` + `success` variants, navy primary

- `tonal`: `bg-primary-50 text-primary-900 hover:bg-primary-100 active:bg-primary-200` — quiet row action.
- `success`: `bg-green-100 text-green-800`, and it keeps that tint when disabled because the disabled state *is* the message (Following ✓).
- `primary` repointed from `indigo-600/700/800` to `primary-900/800/700`.
- `iconOnlyMobile` label is `sr-only sm:not-sr-only` instead of `hidden sm:inline`.
- Visible change: five Button call sites (`AddPodcastModal`,
  `ImportEpisodeModal`, `Inbox`, `PodcastDetail`, `Podcasts`) turn navy.
  `RefreshButton` and `BulkActionsBar` now import `Button` instead of
  carrying its classes; the add-podcast modal's keyboard highlight moved from
  `bg-indigo-50` to `bg-primary-50`. `ring-indigo-*` selection rings on
  `EpisodeCard` are left for Phase 3.

### 5.2 One list typography scale

- `tailwind.config.js`: `fontSize.row: ['15px', { lineHeight: '1.35' }]`.
- `text-row` replaces the arbitrary `text-[15px]` in Inbox; Episodes'
  `text-sm sm:text-base` becomes `text-row sm:text-base` (1 px larger on
  phones, unchanged on desktop).

### 5.3 Flat groups instead of card-per-row

Cards keep meaning "a separate object" (PodcastCard grid, StatusCard,
BriefingCard hero). Repeated rows stop being cards.

| Page | Phase | Change | User-visible effect |
|---|---|---|---|
| Top podcasts | 1 ✅ | `ListRow` + `ListGroup`, shared `FollowButton`, Apple link `sm`+ as a ghost icon | Full titles, 44 px buttons, two more rows per screen |
| Add podcast modal | 1 ✅ | Shared `FollowButton` only (already a divided list) | Same states as the chart |
| Inbox | 2 ✅ | Rows via `ListRow` (`overline` = podcast name + timestamp, `footer` = progress pill); full-bleed group replaces `space-y-3` cards | +64 px title width on phones, tighter list; read/unread/saved/dismissed semantics unchanged |
| Briefings | 2 ✅ | `ListRow`; trailing keeps the status badge, drops "Read →" (the row is the link) | No more three-element collisions |
| Activity feed | 2 ✅ | `ListRow` (`overline` = action badge + relative time); `truncate` → clamp-2; 48 px art; `border-gray-100` normalised | Matches Inbox density |
| Search entity cards | 3 | Title scale + `rounded-md` avatar; stays a grid | Minor |
| Episodes / podcast detail | 3 | `text-row`, thumbnail radius; keeps its card (selection + badges) | 1 px larger title on phones |
| Podcasts grid, dashboard tiles | – | None (cards, not rows) | None |
| Desktop, all pages | 1–2 | Apple link becomes an icon; Button primary turns navy | Otherwise unchanged |

## 6. Implementation notes

- **New:** `components/ListRow.tsx` (+ `ListRowArtwork`),
  `components/ListGroup.tsx`, `components/FollowButton.tsx`, with
  `ListRow.test.tsx` and `FollowButton.test.tsx`.
- **Edited, phase 1:** `Button.tsx`, `tailwind.config.js` (`fontSize.row`),
  `TopPodcasts.tsx`, `AddPodcastModal.tsx`, `RefreshButton.tsx`,
  `BulkActionsBar.tsx`.
- **Edited, phase 2:** `Inbox.tsx`, `Briefings.tsx`, `ActivityFeed.tsx`
  (skeletons moved inside the group too).
- **Tests:** `TopPodcasts.test.tsx` and `AddPodcastModal.test.tsx` query
  buttons by accessible name ("Follow", "Following ✓"); the `sr-only` label
  preserves them. `Inbox.test.tsx` scopes its sr-only state assertion to the
  row because the page's Import button now also carries an sr-only label.
  `tests/mobile-list-rows.spec.ts` runs hermetically at 393 × 852 and asserts
  the title link is ≥ 160 px wide, the Follow control ≥ 44 px, the group is
  full-bleed, the rank sits within the first 40 px, and no Apple link is in
  the phone row. `tests/scroll-restoration.spec.ts` keeps passing because the
  row link keeps its `Open <name>` accessible name.
- **Accessibility bundled in:** chart rows with a `podcast_slug` are real
  `<Link>`s (cmd-click, open in new tab); rows without a slug render a
  `<button>` for the resolve-then-navigate flow. Neither nests interactive
  content.
- **Out of scope, noted:** the Top podcasts header wraps search, category
  and region controls onto two lines on phones; a flag-only region select
  would recover ~40 px of height. Separate pass.

## 7. Phases and gates

| Phase | Scope | Gate | Status |
|---|---|---|---|
| 1 | Primitives + Button changes + Top podcasts + Add podcast modal | Vitest green; Playwright 393 px assertions; visual check on Podcasts/Inbox/PodcastDetail for the navy primary | ✅ Done |
| 2 | Inbox, Briefings, activity feed on `ListRow` | Inbox tests green; no change in Inbox read/unread/dismissed semantics | ✅ Done |
| 3 | Search entity cards, EpisodeCard tokens (`text-row`, `rounded-md`, `ring-primary-*` selection) | Optional; land with the next touch of those files | ⏳ Open |
