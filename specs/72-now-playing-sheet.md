# Now Playing Sheet — Tap-to-Expand Player

**Status**: 🚧 Implemented on `feat/72-now-playing-sheet` (2026-09-08); pending review + merge
**Created**: 2026-09-03
**Updated**: 2026-09-08
**Priority**: Medium (turns "a bar that plays audio" into a podcast player; unblocked by #71)

> **Related:** [#71 player-shell-layer](71-player-shell-layer.md) (prerequisite: the bar must be reachable before it can expand), [#22 floating-media-player](22-floating-media-player.md) (`PlayerContext` already exposes `playbackRate`, `setRate`, `volume`, `setVolume` with no UI), [#61 unified-av-playback-session](61-unified-av-playback-session.md) §2 ("if video is presented nowhere, an expanded mini player may host the video itself"), [#62 youtube-video-rendition](62-youtube-video-rendition.md) (rate limits on the YouTube engine; §7 presented-only rule), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) §5.2 (mention density timeline — moves here), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md) (word wipe + 150 ms perceptual lead; follow-playback toggle), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (overlay contract for links out of the sheet), [#76 episode-detail-page-hierarchy](76-episode-detail-page-hierarchy.md) (`Button`/`ActionRow`/`Artwork` primitives, colour tokens, collapsed episode bar), [#73 mobile-list-row-density](73-mobile-list-row-density.md) (44 px touch floor), [docs/code-guidelines.md §Navigation invariants](../docs/code-guidelines.md) (contract every new link out of a page must meet)

## Executive Summary

Every mature player splits the transport in two: a compact bar with at most
three actions, and an expanded Now Playing surface one tap away that holds
everything else. Thestill has the bar and nothing behind it. As a result:

- Playback speed and volume exist in `PlayerContext` but have no control
  anywhere. Speed is the most-used secondary feature of any podcast app.
- Seeking on a phone means hitting a 4 px range input. Scrubbing a two-hour
  transcript by finger is hopeless, and #71 deliberately did not enlarge the
  bar to fix it.
- Stop-and-dismiss was removed from the phone bar in #71 and has no home.
- The mention-density timeline (#28 §5.2, "left of the audio scrubber") was
  shipped as a floating strip docked *above* the bar because the bar has no
  scrubber wide enough to carry ticks. #71 suppresses it inside the reader.
- Show video / PiP / rendition switching are reachable only from the reader.

Since v1, #76 gave both reader hosts a 56 px collapsed header with play/pause
once the title scrolls away. That covers pausing the *same* episode from deep
in its transcript; it does nothing for seek, skip, speed, Stop, or a
different episode, so the case above stands unchanged.

This spec adds the expanded surface: a bottom sheet on phones, a card
anchored to the bar on desktop, opened by tapping the bar's artwork or title.
It is a transient surface in #71's ladder (`z-[70]`): it may cover the reader
and the bar, and it is dismissed in seconds.

## Table of Contents

1. [Product Requirements](#product-requirements)
2. [Design](#design)
3. [Architecture](#architecture)
4. [Implementation Sequence](#implementation-sequence)
5. [Testing](#testing)
6. [Open Questions](#open-questions)
7. [Non-Goals](#non-goals)
8. [Decision Log](#decision-log)

## Product Requirements

| As a… | I want… | So that… |
|---|---|---|
| Listener (phone) | to tap the bar and get a big scrubber and skip buttons | I can move around a long episode with my thumb |
| Listener | a speed control that remembers my choice | I listen at 1.5× without setting it every episode |
| Listener | to see where the people and topics I care about are mentioned, on the scrubber | I can jump to the part of the episode that matters (#28 §5.2's original intent) |
| Listener | to see the line being spoken right now, in the player | I can glance at what I just heard without opening the transcript |
| Listener | to jump into the transcript at the current moment | I can read on from exactly here |
| Listener (video) | to see the video in the expanded player when I have navigated away from the reader on a phone | phones have no floating tile (#61 §2) |
| Listener | to stop and dismiss the player from the sheet | the phone bar does not carry a destructive action next to Play |
| Keyboard user | Esc to close the sheet and arrow keys on the scrubber | the sheet is operable without a pointer |

## Design

### Entry and exit

- **Open:** tap/click the bar's artwork or title block (the whole left region
  is one `<button aria-expanded>`; the title stops being a link — the sheet
  carries the link to the episode instead). A chevron at the left edge of the
  bar signals expandability from `sm` up.
- **Close:** Esc; tap the scrim (phone); click outside (desktop); the ✕ in
  the sheet header; swipe down on the drag handle (phone, pointer events,
  ≥ 80 px or a velocity threshold). Closing never affects playback.
- Open state lives in `Layout` (the sheet and bar are siblings there); no
  route change, no history entry, nothing in the URL — it is transient UI,
  not view state, so the navigation contract's "state lives in the URL" rule
  does not apply. A route change closes the sheet.

### Surfaces

| | Phone (< `sm`) | Desktop (≥ `sm`) |
|---|---|---|
| Form | bottom sheet, `max-h-[92vh]`, rounded top, drag handle, scrim | card `w-[26rem]` anchored above the bar's left edge (artwork side), shadow, no scrim |
| Rung | `z-[70]` (transient) | `z-[70]` |
| Focus | `role="dialog"`, `aria-modal="true"`, trap, restore to the bar on close | `role="dialog"`, no trap; Esc/outside-click close |
| Body scroll | locked while open | not locked |
| Breakpoint source | `useIsSmUp()` (`hooks/useMediaQuery.ts`, shipped with #76) | same |

**Why a card and not a side panel** (v1 open question 1, resolved): on
desktop, video already has two homes — the theater slot in the reader and
the floating tile off-reader (#61). A side panel would be a third and would
really replace the tile. Karaoke's real surface is the transcript; a panel
carrying a live scrolling transcript would duplicate the reader and fight
the reader overlay for the right edge at `lg`. What both forms can carry is a
current-line strip (below). If a persistent lyrics-style panel is ever
wanted, #52's split-pane alternative is the vehicle, not this surface.

### Visual language

Built from the #76 primitives, no hand-rolled controls:

- `Button` for the transport and the header ✕: `variant="primary"
  size="iconLg"` (new, 56 px disc / 56 px hit) for play/pause;
  `variant="ghost" size="icon"` (44 px) for the skips (a ring glyph with
  "15" set inside) and the ✕. Everything else is one utility row of
  glyph-over-label ghost buttons (52 px tall, tinted when "on"). `iconLg`
  is the one addition to `buttonStyles.ts`.
- `Artwork role="card"` (96 px) in the phone header; a new `role="sheet"`
  (64 px / 8 px radius) in the desktop card header — added to
  `artworkRoles.ts` next to the existing six.
- Colour only through tokens (`bg-surface`, `bg-page`, `text-ink`,
  `text-muted`, `border-hairline`, `bg-accent`/`text-accent-contrast`); no
  `gray-*` literals in new files.
- Entity tick colours from `entityStyle(type).dot` (unchanged from the
  strip).

### Contents, top to bottom

1. **Header** — artwork, episode title (link to the episode; keeps the #52
   overlay contract exactly as the bar's link does in #71), podcast title,
   ✕ close.
   - *Video (phase 2c):* when the session's presentation would otherwise be
     `hidden` on a phone (no theater slot, no floating tile), the header
     registers a 16:9 slot and the media layer positions the stable `<video>`
     over it. Closing the sheet unregisters it; the existing #62 §7 effect
     then switches a YouTube session to audio, exactly as leaving the reader
     does today — no new continuity code.
2. **Scrubber** — full width, 44 px hit area, `<input type="range">` with
   `aria-valuetext` as `m:ss of h:mm:ss`. Elapsed on the left; the right
   label toggles between total and remaining on tap (persisted,
   `thestill:player:remaining`). Disabled with `--:--` while duration is
   unknown.
   - *Entity ticks (phase 2b):* the top-5 entities by mention count (same
     selector as the reader's strip) drawn as a row of dots beneath the
     track, coloured by `entityStyle(type)`. Hover / long-press shows the
     entity name and timestamp; tap seeks. This *is* the #28 §5.2 mention
     density timeline; the floating strip is deleted.
3. **Current line** — the transcript segment under the playhead, one to two
   lines, with the #38 word wipe. Reuses `useKaraokeActiveWordIdx` +
   `KaraokeWord`, so it inherits the 150 ms perceptual lead
   (`HIGHLIGHT_LEAD_SECONDS`) automatically; the active segment is resolved
   with the same lead the transcript viewer's `ActiveSegmentTracker` applies,
   so the two never disagree about which line is current. Plain text when
   word data is absent (404 sentinel); hidden when there is no transcript.
   Data comes from the same React Query keys the reader uses
   (`useEpisodeTranscript`, `useEpisodeTranscriptWords`), fetched only while
   the sheet is open.
4. **Transport** — back 15 · play/pause (56 px) · forward 15. Same handlers
   as the bar.
5. **Speed** — one chip in the utility row showing the current rate; a tap
   steps `0.8× → 1× → 1.2× → 1.5× → 2× → 0.8×` (`nextRate` in
   `utils/playbackRate.ts`). A set-and-forget preference does not earn a
   row of its own. **One global
   preference** (v1 open question 2, resolved), persisted in `localStorage`
   (`thestill:player:rate`) and applied by `PlayerProvider` on every new
   track, rendition switch and YouTube entry, so it survives reloads and
   engine switches. On the YouTube engine the engine reports the rates the
   iframe accepts for the current video; the step skips unsupported rates
   and a pending rate is clamped to the nearest supported one.
6. **Utility row** — evenly spaced, secondary in weight, after the
   transport: Speed (above), then
   - **Transcript** (accessible name "Open transcript here") → the episode
     with `?view=transcript&t=<s>`, carrying `backgroundLocation` per the
     #52 contract. See [Deep link](#deep-link-and-the-navigation-contract).
   - **Video** (pressed = shown) and **Pop out** (picture-in-picture) → the
     existing `setVideoPreference` / `requestPip` from #61, present only
     when a visual rendition exists.
   - **Volume** (desktop only) → a slider row under the utility row, bound
     to `volume` / `setVolume`; mute toggle.

   Not in the sheet: **Follow playback** — a transcript-reading setting
   whose effect is invisible from the player; it stays with the transcript
   viewer (the shared `thestill:transcript:followPlayback` store is
   unchanged). **Stop** — Pause is how you stop; the desktop bar keeps its
   ✕, and on a phone a swipe down on the bar itself (≥ 48 px, mostly
   vertical; the bar follows the finger) stops and dismisses the player.
   The sheet's drag handle closes only the sheet.

### Deep link and the navigation contract

"Open transcript here" is a new link out of a page, so it must satisfy
[docs/code-guidelines.md §Navigation invariants](../docs/code-guidelines.md):

- It is a **push** to the episode route (or an overlay open over the inbox).
  Back returns to the origin with its scroll position restored by
  `useScrollRestoration` (window routes) or `useReadingPosition` (the
  overlay's own container). The route is added to the navigation-contract
  Playwright table.
- **`t` wins on push, position wins on pop.** Today `useDeepLinkSeek` only
  seeks; scrolling to the segment happens only if follow is on. A new
  `useDeepLinkScrollTarget` in `EpisodeReader` resolves `t` to a segment id
  (`findActiveSegmentIndex`, offset-aware) and feeds the citation
  scroll-target mechanism (`{segmentId, nonce}`), so the reader lands on the
  segment regardless of follow. It fires once per `(episodeId, t)` on a
  fresh entry only; on a router POP it does nothing, and `useReadingPosition`
  restores the saved position as it does today. The two never race.

### Keyboard

- Space: toggle (from #71, unchanged; the sheet's own buttons keep native
  space activation).
- ← / → while the scrubber has focus: native ±step; step set to 5 s.
- Esc: close.

### States

| State | Sheet behaviour |
|---|---|
| No track | Bar hidden; sheet cannot open |
| Loading / unknown duration | Scrubber disabled, ticks and current line hidden, transport enabled |
| Media error | Error line under the header, same text as the theater surface |
| YouTube engine | Rate chips constrained to the engine's reported rates; Hide video switches to the audio rendition (#62 §7 policy unchanged) |
| Track changes while open | Sheet stays open and re-binds; ticks, current line and available rates refetch |

## Architecture

Pragmatic shape: extract only what removes duplication that exists today,
keep each commit reviewable on its own.

### New

| Unit | Responsibility |
|---|---|
| `components/NowPlayingSheet.tsx` | Sheet/card chrome (focus trap, Esc, scroll lock mirror `EpisodeReaderOverlay`; slide transition mirrors `NavigationDrawer`), header, transport, speed, chips, Stop. Reads `usePlayer()` / `usePlayerTime()`; owns only UI state (remaining toggle, drag offset). Phase 2c: registers the header slot. |
| `components/NowPlayingScrubber.tsx` | 44 px range + labels + tick row. Props: `currentTime, duration, onSeek, ticks?: ScrubberTick[]` (the sheet builds ticks from entities). |
| `components/NowPlayingSpeedControl.tsx` | Segmented `RATE_OPTIONS` radiogroup; options outside `availableRates` disabled, not hidden. |
| `components/NowPlayingKaraokeLine.tsx` | Current-line strip (§3 above). |
| `hooks/usePlayerRatePreference.ts` + `utils/playbackRate.ts` | `RATE_OPTIONS`, `readPersistedRate`/`writePersistedRate`, `clampRateToAvailable`, `formatRateLabel`. The hook is called inside `PlayerProvider`. |
| `hooks/useFollowPlayback.ts` | `useSyncExternalStore` store over `thestill:transcript:followPlayback`; module-level listener set; `__resetForTests`. |
| `hooks/useEpisodeLinkState.ts` | The #52 `backgroundLocation` / in-inbox / already-here logic lifted out of `MiniPlayer` so the bar's "Show video" link, the sheet's title link and "Open transcript here" cannot drift. |
| `hooks/useDeepLinkScrollTarget.ts` | `t` → `{segmentId, nonce}` once per `(episodeId, t)`, push-only. |
| `utils/mentionDensity.ts` | `TOP_N`, `selectTopEntities(entities)` — the one selector for the strip's replacement and the key-entities strip. |

### Changed

- `contexts/PlayerContext.tsx` — apply the persisted rate on the
  new-episode `play()` branch and in `playYouTube`; `setRate` persists;
  `availableRates: number[] | null` state fed by a new engine event, reset on
  `stop()` and on leaving YouTube; media-layer z-index resolved by
  `mediaLayerZIndex(el)` from `constants/layers.ts` instead of the inline
  `[role="dialog"]` test; `hasTheaterSlot()` — a synchronous getter (see the
  video-slot guard below).
- `contexts/playback-engine/{types,youtube-iframe-api,youtube-engine,youtube-player-fake}.ts`
  — `getAvailablePlaybackRates()` on `YTPlayer`; the engine emits
  `onAvailableRatesChange` once ready and clamps a pending rate; the fake
  returns a deliberately non-superset list so clamping is exercised.
- `constants/layers.ts` — `mediaLayerZIndex(el)`: inside
  `[data-media-host="now-playing"]` → 71; inside `[role="dialog"]` → 60;
  else 40. New rung documented: `71` media layer inside the Now Playing
  sheet.
- `components/buttonStyles.ts` — `iconLg` (56 px). `components/artworkRoles.ts`
  — `sheet` (64 px).
- `components/MiniPlayer.tsx` — artwork/title block → expand button with
  `aria-expanded`/`aria-haspopup="dialog"`; chevron from `sm`; link-state via
  `useEpisodeLinkState`; accepts `isOpen`/`onExpand`.
- `components/Layout.tsx` — `isNowPlayingOpen`; closes on `location.pathname`
  change; renders the sheet after the bar.
- `components/SegmentedTranscriptViewer.tsx` — follow checkbox reads
  `useFollowPlayback()`; `FOLLOW_STORAGE_KEY` moves into the hook.
- `components/episode-entities/KeyEntitiesStrip.tsx` — uses
  `selectTopEntities`.
- `components/EpisodeReader.tsx` — `useDeepLinkScrollTarget`; the
  `PlayerScopedTimeline` block and #71's `inOverlay` suppression are deleted
  with the strip.

**Deleted (2b):** `components/episode-entities/MentionDensityTimeline.tsx`
and its test.

**Data:** ticks from `useEpisodeEntities(track.episodeId)` (keyed by episode
id, which `PlayerTrack` carries — v1 wrongly said slugs), sharing the
reader's cache entry. Duration from `player.duration`, falling back to
`track.durationHint`. Current line from `useEpisodeTranscript` /
`useEpisodeTranscriptWords` keyed by the track's slugs.

**Video slot guard (2c):** the sheet's slot effect runs while
`isOpen && !isSmUp && videoPresentable && videoPreference === 'shown'` and
bails if `player.hasTheaterSlot()` is already true, so it never steals the
reader's slot. The guard is a synchronous getter rather than reactive state
on purpose: state would re-run the effect on the sheet's *own* registration
and oscillate. `presentation` is not part of the gate either — on a phone
with nothing presenting, the machine's resting value is `floating` (the tile
is desktop-only), not `hidden`. Closing unregisters; the machine returns to
`floating`, the media layer hides, audio continues; a YouTube session drops
to audio through the existing #62 §7 effect.

No backend or API changes.

## Implementation Sequence

Seven commits on `feat/72-now-playing-sheet`, each green on its own
(shipped 2026-09-07/08 as `6ddd69c`, `6517e08`, `8d3e76b`, `a51b3ca`,
`ff12abe`, `cad59bf`, `6e90e86`):

| # | Commit | Gate |
|---|---|---|
| 1 | Rate infrastructure: `utils/playbackRate`, `usePlayerRatePreference`, YouTube available-rates plumbing, `PlayerContext` wiring | `PlayerContext.test.tsx`: rate survives new track / `switchRendition` / `playYouTube`; clamp to the fake's list |
| 2 | `mediaLayerZIndex` in `layers.ts`, `PlayerContext` uses it; `data-media-host` documented | z-index 71 / 60 / 40 assertions |
| 3 | `useEpisodeLinkState` extraction; `MiniPlayer` expand button + chevron; `iconLg`, `Artwork sheet` | `MiniPlayer.test.tsx` updated for `aria-expanded`; link-state tests moved to the hook |
| 4 | **2a** `NowPlayingSheet` + `NowPlayingScrubber` (no ticks) + `Layout` wiring: header, scrubber, transport, speed, volume, Stop, all close paths | `NowPlayingSheet.test.tsx`; Playwright at 390 and 1440: open → seek → speed → Esc/scrim/swipe close |
| 5 | **2b** `useFollowPlayback`, `selectTopEntities`, ticks, "Open transcript here" + `useDeepLinkScrollTarget`, strip deleted, transcript viewer swap | ticks = strip's top-5; deep link lands scrolled with follow off; POP does not fire; navigation-contract row added |
| 6 | **2c** header video slot + Show/Hide video + PiP chips | #61 continuity tests extended: sheet open/close never restarts playback; YouTube falls back to audio on close (#62 §7) |
| 7 | `NowPlayingKaraokeLine` in both forms | line matches `ActiveSegmentTracker`'s segment under the 150 ms lead; plain-text fallback |

## Testing

- Unit (vitest): `playbackRate.test.ts`, `PlayerContext.test.tsx` (rate
  preference across native / YouTube / stop; media-layer rung per host),
  `youtube-engine.test.ts` (publish + clamp), `layers.test.ts`,
  `useEpisodeLinkState.test.tsx`, `useFollowPlayback.test.tsx` (two consumers
  in step), `useDeepLinkScrollTarget.test.tsx` (once per entry, gap
  fallback, offset, junk), `NowPlayingSpeedControl.test.tsx`,
  `NowPlayingKaraokeLine.test.tsx` (lead, gaps, wipe),
  `NowPlayingSheet.test.tsx` (both forms, every close path, transport,
  speed, Stop, title link contract, ticks, deep link, follow toggle, phone
  video slot claim / yield / release, desktop chips), `MiniPlayer.test.tsx`
  (expand button).
- Playwright `tests/now-playing-sheet.spec.ts` (hermetic, CI gate): phone
  sheet open → seek → speed → Esc; desktop card above the bar, click-outside,
  speed persists across reopen, Stop; and the navigation-contract check for
  "Open transcript here" (scroll origin → link → transcript at `t` → Back →
  position kept). The contract table in `navigation-contract.spec.ts`
  points here for that link.

## Open Questions

1. ~~Desktop form~~ — resolved 2026-09-07: card (see [Surfaces](#surfaces)).
2. ~~Rate scope~~ — resolved 2026-09-07: global; a per-podcast override is an
   additive key later.
3. **Should the desktop bar keep its ✕** once Stop lives in the sheet? Kept
   for now; remove if analytics show no use.
4. **Sleep timer** — a natural section but no requirement yet.

## Non-Goals

- Queue / up next (still #22's non-goal).
- Chapter markers — no chapter data is ingested; the tick row is designed so
  chapters can be a second series later.
- Cross-device playback position sync.
- Any change to karaoke rendering or the transcript viewer beyond the
  follow-store swap and the deep-link entry.
- Dark mode (separate spec per #76 §5.7); the sheet uses tokens so it will
  follow.

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-03 | Drafted from the player/overlay design review. Sheet is transient (`z-[70]`) rather than a fourth long-lived surface. Entity timeline relocates onto the scrubber instead of being repositioned as a floating strip. Rate persistence added to the provider rather than to the sheet so it survives engine switches. |
| 2026-09-08 | Hierarchy rework after the first phone review. The title's `block` class was overriding `line-clamp-2` (Tailwind emits `.block` after `.line-clamp-2`), so long titles ran to six lines; removed. Speed is a tap-to-step chip, not a segmented control. Stop is gone (Pause stops; the desktop bar keeps ✕). Follow-playback left the sheet for the transcript viewer, where its effect is visible. Everything below the transport is one evenly spaced utility row. Swipe down on the phone bar stops and dismisses the player. The current line reserves two lines so the transport never jumps; the skips use a ring glyph with the seconds inside. |
| 2026-09-08 | Built. `hasTheaterSlot()` getter chosen over a `presentation === 'hidden'` gate for the phone video slot (reactive gate oscillates; resting state on a phone is `floating`). Speed control extracted as its own component so engine-constrained rendering is unit-testable without YouTube. The navigation-contract check for "Open transcript here" lives in the sheet's own Playwright spec because the contract table cannot express "open a sheet first". |
| 2026-09-07 | v2 after #73/#74/#76 and the navigation contract landed. Card over side panel (video already has theater + tile on desktop; karaoke belongs to the transcript). One global rate. Follow lifted into a shared `useSyncExternalStore` store over the existing key. All three phases plus a current-line karaoke strip on this branch. Built from #76 primitives (`Button`, `Artwork`, tokens) with two additive sizes. Deep link: `t` wins on push, reading position wins on pop; route joins the navigation-contract table. `useIsSmUp` reused rather than extracted. Pragmatic blueprint chosen over minimal (ad hoc rate handling, rewriting a component slated for deletion) and clean (refactors of the transcript tracker and key-entities strip that widen review without changing behaviour). |
