# Mobile List Row Density — `ListRow` Primitive

**Status**: 📝 Draft (2026-09-04) — design review, no code yet
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

Result on the same phone: title width 179 px (3.1×), 10 rows per screen
instead of 8.7, 44 px touch targets, one row shape across the app.

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
| **C · ListRow + flat group** | **179 px** | **10.4** | 44 px | 5 + 2 new | Inbox, Briefings, Activity, Search |

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

**C — `ListRow` primitive, flat `ListGroup`.** Recommended. A and C deliver
near-identical title width; the difference is whether the fix is a value
the rest of the app inherits. C also brings Top podcasts onto the typography
Inbox already uses, so the two most-visited lists look like one product.

## 4. Row specification (Option C)

| Slot | Classes / rule |
|---|---|
| Group | `bg-white border border-gray-200 rounded-lg divide-y divide-gray-100 overflow-hidden`. Replaces `space-y-2` / `space-y-3` plus per-row borders, on all breakpoints (one row shape). |
| Row | `flex items-center gap-3 px-3 py-2.5 sm:px-4 sm:py-3 min-h-[64px]`. Whole row is a `<Link>`; nested actions stop propagation. |
| Leading · rank | `w-7 shrink-0 text-right text-sm font-semibold tabular-nums text-gray-400`. Omitted for unranked lists. Never larger than the title. |
| Leading · artwork | `w-12 h-12 rounded-md object-cover shrink-0` for podcast and inbox rows; `w-10 h-10` for episode rows within a podcast; `w-8 h-8` in ⌘K. Always `rounded-md`. |
| Body · title | `text-row sm:text-base font-medium leading-snug text-gray-900 line-clamp-2`. Never `truncate` on a primary title. |
| Body · subtitle | `text-xs sm:text-sm text-gray-500 truncate mt-0.5`. Artist, then `· category`. Category may be dropped below `sm`. |
| Trailing (phone) | At most one element, 44 × 44 px: icon Button, status badge or timestamp. Text labels return at `sm` via `iconOnlyMobile`. |
| Follow | Shared `FollowButton` (extracted from `AddPodcastModal`) on `Button`: idle = `tonal` + plus icon; pending = spinner; done = green tint + check, `aria-label="Following"`; error = `danger` "Retry". Label text stays in the DOM as `sr-only` below `sm` so name-based tests keep passing. |
| Apple link | Hidden below `sm`; ghost icon button with `aria-label="Open in Apple Podcasts"` at `sm`+. Follow-up: surface Apple/YouTube links on the podcast detail page (requires storing `apple_url` on the local podcast row). |

## 5. Design-system changes and ripple

### 5.1 `Button.tsx`: `tonal` variant, navy primary

- Add `tonal`: `bg-primary-50 text-primary-900 hover:bg-primary-100 active:bg-primary-200`.
- Repoint `primary` from `indigo-600/700/800` to `primary-900/800/700`.
- Visible change: five Button call sites (`AddPodcastModal`,
  `ImportEpisodeModal`, `Inbox`, `PodcastDetail`, `Podcasts`) turn navy.
  `RefreshButton` and `BulkActionsBar` should import `Button` instead of
  carrying its classes. `ring-indigo-*` selection rings on `EpisodeCard` can
  follow later.

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
| Top podcasts | 1 | `ListRow` + `ListGroup`, shared `FollowButton`, Apple link `sm`+ | Full titles, 44 px buttons, two more rows per screen |
| Add podcast modal | 1 | Shared `FollowButton` only (already a divided list) | Same states as the chart |
| Inbox | 2 | Rows via `ListRow`; flat group replaces `space-y-3` cards | +30 px title width, tighter list |
| Briefings | 2 | `ListRow`; trailing keeps status badge, drops "Read →" (row is a link) | No more three-element collisions |
| Activity feed | 2 | `ListRow`; `truncate` → clamp-2; `border-gray-100` normalised | Matches Inbox density |
| Search entity cards | 3 | Title scale + `rounded-md` avatar; stays a grid | Minor |
| Episodes / podcast detail | 3 | `text-row`, thumbnail radius; keeps its card (selection + badges) | 1 px larger title on phones |
| Podcasts grid, dashboard tiles | – | None (cards, not rows) | None |
| Desktop, all pages | 1–2 | Apple link becomes an icon; Button primary turns navy | Otherwise unchanged |

## 6. Implementation notes

- **New:** `components/ListRow.tsx`, `components/ListGroup.tsx`,
  `components/FollowButton.tsx`.
- **Edited, phase 1:** `Button.tsx`, `tailwind.config.js`,
  `TopPodcasts.tsx`, `AddPodcastModal.tsx`, `RefreshButton.tsx`,
  `BulkActionsBar.tsx`.
- **Tests:** `TopPodcasts.test.tsx` and `AddPodcastModal.test.tsx` query
  buttons by accessible name ("Follow", "Following ✓"); the `sr-only` label
  preserves them. Add one Playwright case at a 393 px viewport asserting the
  title element is ≥ 160 px wide and the Follow control ≥ 44 px tall.
- **Accessibility bundled in:** chart rows with a `podcast_slug` become real
  `<Link>`s (cmd-click, open in new tab); rows without a slug keep the
  resolve-then-navigate handler.
- **Out of scope, noted:** the Top podcasts header wraps search, category
  and region controls onto two lines on phones; a flag-only region select
  would recover ~40 px of height. Separate pass.

## 7. Phases and gates

| Phase | Scope | Gate |
|---|---|---|
| 1 | Primitives + Button changes + Top podcasts + Add podcast modal | Vitest green; Playwright 393 px assertions; visual check on Podcasts/Inbox/PodcastDetail for the navy primary |
| 2 | Inbox, Briefings, activity feed on `ListRow` | Inbox tests green; no change in Inbox read/unread/dismissed semantics |
| 3 | Search entity cards, EpisodeCard tokens | Optional; land with the next touch of those files |
