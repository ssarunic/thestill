# Episode Detail Page Hierarchy — Hero, Action Row, Information

**Status**: 💡 Proposal — for review, not scheduled
**Created**: 2026-09-07
**Updated**: 2026-09-07
**Priority**: Medium (the episode page is the product's most-visited surface and reads as an admin record rather than a content page)

> **Related:** [#09 single-user-web-ui](09-single-user-web-ui.md) §Visual Design (palette, 8 px grid, Inter scale), [#73 mobile-list-row-density](73-mobile-list-row-density.md) (full-bleed-below-`sm` rule, `Button` variants, navy primary token), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (the same reader renders as a page and as an overlay), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) §5.2 (key entities strip, entity branch progress), [#62 youtube-video-rendition](62-youtube-video-rendition.md) §6 ("Watch video" entry point), [#71 player-shell-layer](71-player-shell-layer.md) (mini player height budget), [#58 original-language-summaries](58-original-language-summaries.md) (summary language toggle in the tab bar)

## Executive Summary

A side-by-side review of Thestill's episode page against Apple Podcasts'
episode page (iOS, dark) on the same phone found that Thestill's strengths
are the things Apple does not do at all (Summary and Transcript as first-class
tabs, citations, karaoke words, entity highlights, honest processing states,
a matching loading skeleton) and that Apple's advantages are all hierarchy:
artwork as the hero, one dominant action that carries the duration, metadata
in one labelled place, people shown as content, and no system chrome on a
listener surface.

On a 393 px phone Thestill's first screen spends its height on a 96 px
artwork thumbnail, a centred title, a `Ready` state pill, a bullet-separated
meta line, a divider, a play pill, a divider, an `Indexed` chip, a divider,
and the description. Two of those items (`Ready`, `Indexed`) report pipeline
state that the Summary tab already proves, and the list uses six hairline
rules inside a bordered card on a grey page.

This spec proposes rebuilding the header of `EpisodeReader` as a
`PageHero` + `ActionRow` + description, moving every remaining fact into an
`Information` definition list below the content tabs, adding a `People` row
sourced from transcript speakers and person entities, and collapsing the
header into a sticky bar on scroll. The primitives it introduces (`PageHero`,
`ActionRow`, `MetaEyebrow`, `DefinitionList`, `Artwork`, surface tiers, a
detail-page type scale, colour tokens) are meant to be adopted by Podcast
detail and Briefing detail next, the same way #73's `ListRow` was adopted
across five lists. Dark mode is explicitly out of scope; the token work in
§5.7 is its prerequisite.

## 1. Comparison

Reviewed on the same iPhone at 393 × 852. Apple screenshots: a bonus
episode of *The Diary Of A CEO* (hero, description, "Hosts & Guests",
transcript teaser, "Information" table). Thestill: a *Prof G Markets*
episode opened from the Inbox overlay.

### 1.1 What Thestill does well

| Area | Thestill | Apple | Verdict |
|---|---|---|---|
| Summary and transcript | Both are tabs directly under the header, full text, citations, karaoke words, entity highlights, language toggle | Transcript is a two-paragraph teaser behind a chevron; no summary | Thestill leads. Keep the tabs where they are |
| Reading typography | 16–18 px body, 1.7 line height, serif transcript, `prose` summaries | Similar serif transcript view | Parity; nothing to change |
| Loading and failure states | Skeleton matches the real header layout; failure banner; pipeline stepper with retry | Nothing to show — no pipeline | Thestill is right to keep these, but they belong below the primary action (§3.4) |
| Navigation chrome | One `← Inbox` link; page mode gets a breadcrumb | Floating back pill plus a share/more cluster on the artwork | Thestill is calmer. Keep it |
| Touch targets | 44 px minimum on `Button` (#73); play pill is 44 px | 44 px | Parity |
| Existing design language | 8 px grid, navy primary token, `ListRow`/`ListGroup`, `Button` variants (#73) | n/a | Exists but the episode header uses none of it |

### 1.2 What Apple does better

1. **Artwork is the hero.** Full-width artwork with a colour-sampled
   backdrop sets the tone before a word is read. Thestill renders a 96 px
   square (`w-20 h-20 sm:w-24 sm:h-24`) centred inside a white card: the
   size of a list thumbnail, carrying no weight.
2. **One dominant action, and it answers the next question.** The play pill
   reads `▶ 28m`. Download and bookmark are quiet 44 px icon circles next to
   it. Thestill has `Play episode` and an optional `Watch video`, both
   full-height pills of equal weight; the duration lives two lines up in the
   meta line.
3. **Metadata has one home.** A small eyebrow (`3 July · Bonus`) above the
   title, and a labelled `Information` table at the bottom (Show, Channel,
   Frequency, Published, Length, Rating). Thestill spreads the same facts
   across a wrapped line with middle-dot separators, a share icon and a
   `Show Notes` link.
4. **Zero system status on a listener surface.** Apple's page never reports
   its backend. Thestill shows `Ready` (a `summarized` state pill) and
   `Indexed` (entity branch complete) on the first screen. Both are
   operator facts; the Summary tab already proves the first and nothing on
   the page depends on the second.
5. **Space instead of rules.** Apple separates sections with whitespace and
   heading weight. Thestill's header card has up to six `border-t` rules
   inside a bordered card on a grey page, which reads as a form.
6. **People are content.** `Hosts & Guests` with circular portraits. Thestill
   has speaker-labelled transcripts and a person-entity index (#28), better
   data than Apple has, and shows none of it in the header.
7. **Show identity is visual and tappable.** A 40 px podcast thumbnail, the
   show name, a chevron. Thestill has a plain text link.

## 2. Measured problem

Content width at 393 px is 361 px. Height budget of the first screen
(852 px minus ~120 px browser chrome minus the 56 px `← Inbox` header):

| Block in `EpisodeReader` header (current) | Height (px) | Listener value |
|---|---:|---|
| Card padding top | 16 | — |
| Artwork 80 px, centred | 80 + 16 gap | Low at this size |
| Title (2 lines at `text-xl`) + show link | ~88 | High |
| `Ready` state pill | 28 + 8 | None (duplicates the Summary tab) |
| Meta line (date, duration, share, show notes) | ~24 + 16 | Medium; duration is the only decision-relevant fact |
| Divider + `Play episode` pill | 1 + 16 + 44 + 16 | High |
| Divider + `Indexed` chip | 1 + 16 + 24 + 16 | None |
| Divider + description (3-line clamp + `Show more →`) | 1 + 16 + 72 + 28 | Medium |
| Card padding bottom | 16 | — |
| **Total** | **~600** | The tabs start below the fold |

Findings:

1. About 90 px (two pills, two dividers) carries no listener information.
2. The strongest visual element on screen is the green `Ready` pill, not the
   artwork, the title, or the play button.
3. Seven horizontal rules (card top and bottom, four `border-t`, one tab
   border) are visible on the first screen.
4. Title and show name are centred on phones and left-aligned on `sm+`, so
   the page changes character at the breakpoint; the description below is
   always left-aligned.

## 3. Page specification

The header becomes four stacked regions with no dividers between them, then
the existing content panel, then two new sections. Phone layout first;
desktop differences noted inline.

```text
┌──────────────────────────────────────────┐
│ ← Inbox                                  │  overlay chrome (unchanged)
├──────────────────────────────────────────┤
│           ┌────────────────┐             │  3.1 PageHero
│           │                │             │  artwork 60 vw, max 240 px,
│           │    artwork     │             │  blurred copy behind at 20 %
│           │                │             │  (desktop: 200 px, left)
│           └────────────────┘             │
│  SUN 6 SEP · S3 E12 · EXPLICIT           │  eyebrow, 13 px, tracked
│  Why Nobody Trusts the News — And        │  title 22 px / 28 px desktop
│  How to Fix It                           │
│  [▪] Prof G Markets                    › │  show row: 28 px thumb + chevron
│                                          │
│  (▶ 58 min)   (⧉)   (⇪)   (↗)            │  3.2 ActionRow
│                                          │
│  Ed Elson sits down with Jim VandeHei,   │  3.3 description, 3-line clamp
│  co-founder and CEO of Axios, to …       │
│  More                                    │
├──────────────────────────────────────────┤
│  [pipeline stepper / failure banner]     │  3.4 only when not summarized
├──────────────────────────────────────────┤
│  Key entities strip (unchanged, #28)     │
├──────────────────────────────────────────┤
│  Summary   Transcript          EN | HR   │  content panel (unchanged)
│  …                                       │
├──────────────────────────────────────────┤
│  People                                  │  3.5
│  (○) Ed Elson  (○) Jim VandeHei          │
├──────────────────────────────────────────┤
│  Information                             │  3.6
│  Show          Prof G Markets            │
│  Author        Prof G Media              │
│  Published     6 Sep 2026, 07:00         │
│  Length        58 min 25 s               │
│  Language      English                   │
│  Explicit      No                        │
│  Show notes    profgmedia.com ↗          │
└──────────────────────────────────────────┘
```

### 3.1 Hero

- Artwork: `SmartImage` with the existing `[episode.image_url, podcast_image_url]`
  fallback chain, rendered at 60 vw (max 240 px) centred on phones, 200 px
  left-aligned on `sm+`, 12 px radius, `loading="eager"`. Behind it, the same
  image at `blur-2xl opacity-20` clipped to the hero, fading to the page
  background at the bottom. The blur is decorative (`aria-hidden`) and
  disabled under `prefers-reduced-transparency`.
- Eyebrow: one line, `MetaEyebrow` (§5.3). Content in order: short date
  (`Sun 6 Sep`, year only if not the current year), `S{n} E{n}` from
  `EpisodeNumber`, episode type when not `full` (`Bonus`, `Trailer`),
  `Explicit` from `ExplicitBadge`. Items joined by ` · `.
- Title: `text-title` (§5.6), `font-bold`, `text-gray-900`, left-aligned at
  every width. Centred text is dropped: it forced the breakpoint character
  change in §2 finding 4.
- Show row: 28 px podcast artwork, show title in `text-base text-gray-700`,
  trailing chevron, whole row is the existing plain `Link` to
  `/podcasts/{slug}` (same navigation semantics as today, spec #52
  interaction table).
- No state pill. `stateColors` stays for list and admin surfaces (§5.5).

### 3.2 Action row

`ActionRow` (§5.2): one primary and up to three icon actions, 12 px gaps,
left-aligned, wrapping never (icons shrink before the primary does).

| Slot | Content | Component |
|---|---|---|
| Primary | `▶ 58 min` / `Pause` / `Resume` — label from `duration_formatted` rounded to minutes; playing and resume states as today | `Button variant="primary" size="lg"`, pill radius |
| Icon 1 | Watch video (YouTube glyph), only under the #62 §6 condition | `Button variant="secondary" size="icon"` |
| Icon 2 | Share (existing `ShareButton` behaviour) | same |
| Icon 3 | Show notes (external-link glyph), only if `website_url` | same |

Labels for the icon buttons are `sr-only` plus `title`. Removing the text
`Watch video` pill is the one change here that trades discoverability for
hierarchy; the YouTube glyph is universally recognised and the same action
is also reachable from the theater menu, so the trade is accepted.

### 3.3 Description

`ExpandableDescription` keeps its three-line clamp and sanitiser. Changes:
the toggle reads `More` / `Less` (no arrow glyphs), and the block follows the
action row with 16 px of space and no `border-t`.

### 3.4 System status

- Pipeline stepper (`PipelineActionButton`) and `FailureBanner` render in
  their own region under the description, only when
  `episode.state !== 'summarized'` or `is_failed`. Same condition as today;
  only the position changes (below the primary action, not above it).
- `EntityBranchProgress` renders its in-progress bar under the tabs' header
  row, as a one-line strip, only while a branch task is running or failed.
  The collapsed `Indexed` pill is removed from the reader. It remains
  available on the Queue page and on the entity pages.
- Spec #68 live refresh is unaffected: the header re-renders on the same
  query key.

### 3.5 People

A horizontal row of circular 56 px avatars with a name under each,
`section` heading `People`. Sources, merged and de-duplicated by name:

1. Person entities from `useEpisodeEntities` with `speaker_kind` of host or
   guest, sorted by `salience`, capped at eight.
2. Distinct transcript `speaker` labels that are not already covered and are
   not placeholder labels (`SPEAKER_00`, `Unknown`).

Avatar image: the entity's image when the entity index carries one;
otherwise an initials disc using the existing speaker colour map
(`utils/speakerColors`), which keeps the avatar colour consistent with the
transcript's speaker labels. Tapping a person entity opens its entity page;
tapping a plain speaker label scrolls the transcript to that speaker's first
segment (the `KeyEntitiesStrip` `onSeek` path). Hidden when both sources are
empty.

### 3.6 Information

`DefinitionList` (§5.4) with `section` heading `Information`. Rows, omitted
when the value is null:

| Label | Value | Source |
|---|---|---|
| Show | podcast title, link | `podcast_title`, `podcast_slug` |
| Author | `author` | podcast `author` (needs adding to `EpisodeDetail`, §7) |
| Published | full date and local time | `pub_date` |
| Length | `58 min 25 s` | `duration` |
| Language | language name | summary `podcast_language` / `canonical_language` |
| Type | Bonus / Trailer | `episode_type`, only when not `full` |
| Explicit | Yes / No | `explicit` |
| Show notes | host name of `website_url`, external link | `website_url` |
| Source | `Imported` / feed | present only for #31 imported episodes |

### 3.7 Sticky collapsed header

Once the title scrolls out of view, a 56 px bar pins to the top of the
scroll container (the window in page mode, the overlay panel in overlay
mode): 32 px artwork, one-line truncated title, a 36 px play/pause icon
button. In overlay mode it merges into the existing `← Inbox` header rather
than stacking under it. Implemented as a `useCollapsingHeader` hook
(`IntersectionObserver` on the title) so Podcast and Briefing detail can
reuse it.

## 4. Options considered

| Option | Description | Verdict |
|---|---|---|
| A. Patch the header in place | Remove the two pills, merge the dividers, enlarge the artwork | Cheapest; leaves three pages with three header anatomies and no reusable pieces. Rejected |
| B. Copy Apple's dark image hero | Full-bleed artwork, dark backdrop, white text | The reading surface below is light and text-heavy; a dark hero over a light page reads as two apps. Also needs the colour tokens that do not exist yet. Rejected for now, revisit after §5.7 |
| **C. Hero + ActionRow + Information as primitives** | This spec | Same cost as A for the episode page, and Podcast and Briefing detail follow for the price of adoption |

## 5. Design-system changes and ripple

### 5.1 Surface tiers

Three named surfaces replace the single `bg-white rounded-lg border
border-gray-200` idiom:

| Tier | Below `sm` | `sm+` | Used by |
|---|---|---|---|
| `page` | `bg-gray-50` | same | body |
| `panel` | plain: no border, no radius, `px-4` | white, hairline border, 8 px radius | content tabs, Information |
| `plain` | no frame ever | no frame ever | hero, action row, description, People |

The below-`sm` rule generalises #73 §5.3 ("flat groups instead of card-per-row")
from lists to detail pages: on phones nothing on the page draws a frame
inside the screen's frame.

### 5.2 `Button`: `icon` size

`sizeStyles` gains `icon: 'w-11 h-11 p-0 rounded-full justify-center'`
(44 px) and `iconSm` (36 px, for the sticky bar). With `iconOnlyMobile`
already present this closes the last case where pages hand-roll circular
buttons (the play and watch buttons in `EpisodeReader`, the share button).
`ActionRow` is a layout component only: `primary` slot plus `actions`
children, `gap-3`, `flex-nowrap`.

### 5.3 `MetaEyebrow`

`text-eyebrow` (§5.6), uppercase, `tracking-wide`, `text-gray-500`, items
joined by a `·` with `aria-hidden` and a `sr-only` comma. Replaces the
bullet-separated `flex flex-wrap` meta line used on the episode page,
`PodcastDetail` and `BriefingDetail`.

### 5.4 `DefinitionList`

`<dl>` with rows of `dt` (`text-sm text-gray-500`, left) and `dd`
(`text-sm text-gray-900`, right-aligned, `tabular-nums` for times and
counts), hairline `divide-y divide-gray-100`. Values may be links. Used by
Information here, by the podcast facts block on `PodcastDetail`, and by
`Settings`.

### 5.5 Status semantics: operator versus listener

Rule for every content page (episode, podcast, briefing): pipeline state,
indexing state and task chips are operator information. They render in the
Queue and Failed Tasks pages, inside the pipeline stepper, or behind a
disclosure, and never above the primary action of a content page. The
`stateColors` map moves out of `EpisodeReader` into a shared
`utils/stateColors.ts` for the surfaces that keep it (episode list rows,
Queue, Failed Tasks).

### 5.6 Detail-page type scale

Alongside the existing `text-row` (#73 §5.2) in `tailwind.config.js`:

| Token | Phone | `sm+` | Weight | Use |
|---|---:|---:|---|---|
| `text-eyebrow` | 13 px | 13 px | 500 | `MetaEyebrow` |
| `text-title` | 22 px / 1.2 | 28 px / 1.2 | 700 | page title in `PageHero` |
| `text-section` | 17 px / 1.3 | 17 px / 1.3 | 600 | in-page headings (`People`, `Information`) |

The `section` size sits clearly above body (16 px) and clearly below the
title, which is what makes Apple's hierarchy scan.

### 5.7 Colour tokens (dark-mode prerequisite, not dark mode)

The frontend has no `dark:` variants today, and `index.css` hard-codes
scrollbar greys. Introduce CSS variables on `:root` for `--bg`, `--surface`,
`--text`, `--text-muted`, `--border`, `--accent`, `--accent-contrast`, map
them into the Tailwind theme (`bg-surface`, `text-muted`, `border-hairline`,
…) and migrate the components touched by this spec plus `Button`, `ListRow`,
`Layout` and `MiniPlayer` to them. Existing `gray-*` classes elsewhere keep
working. Dark mode itself is a separate spec: a transcript reader needs its
own contrast pass before a dark surface is better than the current light
one.

### 5.8 `Artwork`

A thin wrapper over `SmartImage` fixing size and radius per role:
`inline` 28 px / 6 px radius, `row` 48 px / 8 px (#73's `ListRowArtwork`
becomes an alias), `card` 96 px / 8 px, `hero` 200–240 px / 12 px, optional
`backdrop`. Ends the seven size/radius combinations #73 §2 counted.

## 6. Implementation notes

- All header changes are inside `EpisodeReader.tsx`; `EpisodeDetail.tsx`
  and `EpisodeReaderOverlay.tsx` are untouched except for the sticky bar's
  merge into the overlay header (§3.7).
- `EpisodeDetail` API type needs `podcast_author` and `podcast_language`
  (or the Information section reads them from the `usePodcast` cache entry,
  which the breadcrumb pattern in `EpisodeDetail.tsx` shows is free when
  the key is shared). Prefer extending the episode detail response; one
  request, no second cache dependency.
- People §3.5 depends on `useEpisodeEntities`, which already loads for the
  key entities strip; no new request. Speaker labels come from the
  transcript response already in cache when the Transcript tab has been
  opened; when it has not, the row renders from entities only and fills in
  later. This must not cause layout shift above the fold: People is below
  the tabs.
- The loading skeleton is updated to the new anatomy (hero, eyebrow, title,
  show row, action row) so first paint matches the loaded layout.
- `stateColors` extraction (§5.5) touches `EpisodeCard`, `Episodes`,
  `QueueViewer`, `FailedTasks`; mechanical.
- Reduced motion: the blur backdrop and the sticky bar's slide-in respect
  `prefers-reduced-motion`.
- Tests: extend `EpisodeReader.test.tsx` for pill removal, action row
  labels, Information rows omitting nulls, People de-duplication and
  placeholder-speaker filtering; a `useCollapsingHeader` unit test; visual
  check at 393 px and 1280 px.

## 7. Phases and gates

| Phase | Scope | Gate |
|---|---|---|
| 1 | §5.2 `icon` size, §5.3 `MetaEyebrow`, §5.4 `DefinitionList`, §5.6 type scale, §5.8 `Artwork`; hero + action row + description + status relocation on the episode page (§3.1–3.4); skeleton updated | Tabs visible without scrolling on a 393 × 852 phone for a two-line title; no state or index pill above the fold; `make check` green |
| 2 | §3.6 Information (API fields added), §3.5 People, §3.7 sticky bar | People row hidden when both sources empty; Information omits null rows; sticky bar merges into overlay header |
| 3 | Adopt `PageHero`, `ActionRow`, `MetaEyebrow`, `DefinitionList` on `PodcastDetail` and `BriefingDetail`; `stateColors` extraction; §5.1 surface tiers applied to those pages | Three detail pages share one header anatomy; no page-local circular button styles remain |
| 4 | §5.7 colour tokens on the components this spec touched plus `Button`, `ListRow`, `Layout`, `MiniPlayer` | No hard-coded hex in `index.css`; light rendering pixel-identical to phase 3 |

## 8. Open questions

1. Should the primary button read `58 min` (Apple) or keep `Play episode`?
   The spec assumes the duration; a listener who has partly played the
   episode could instead see `Resume · 31 min left` from the reading
   position store, which is a stronger reason to put the number in the pill.
2. Does the entity index carry person images today, or is the initials disc
   the only avatar in phase 2? If images arrive later the component does
   not change.
3. Should `Indexed` survive anywhere on the reader for people who want to
   know entity highlighting is complete, for example as a tooltip on the key
   entities strip heading? The spec removes it entirely.
4. Overlay mode on desktop (`lg:max-w-4xl` panel): does the hero use the
   phone layout (centred artwork) or the desktop one? The spec proposes the
   desktop layout since the panel is 896 px wide.
