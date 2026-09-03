# Now Playing Sheet — Tap-to-Expand Player

**Status**: 📝 Draft (2026-09-03)
**Created**: 2026-09-03
**Updated**: 2026-09-03
**Priority**: Medium (turns "a bar that plays audio" into a podcast player; unblocked by #71)

> **Related:** [#71 player-shell-layer](71-player-shell-layer.md) (prerequisite: the bar must be reachable before it can expand), [#22 floating-media-player](22-floating-media-player.md) (`PlayerContext` already exposes `playbackRate`, `setRate`, `volume`, `setVolume` with no UI), [#61 unified-av-playback-session](61-unified-av-playback-session.md) §2 ("if video is presented nowhere, an expanded mini player may host the video itself"), [#62 youtube-video-rendition](62-youtube-video-rendition.md) (rate limits on the YouTube engine), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) §5.2 (mention density timeline — moves here), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md) (follow-playback toggle), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (overlay contract for links out of the sheet)

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

This spec adds the expanded surface: a bottom sheet on phones, a card
anchored to the bar on desktop, opened by tapping the bar's artwork or title.
It is a transient surface in #71's ladder (`z-[70]`): it may cover the reader
and the bar, and it is dismissed in seconds.

## Table of Contents

1. [Product Requirements](#product-requirements)
2. [Design](#design)
3. [Architecture](#architecture)
4. [Implementation Phases](#implementation-phases)
5. [Testing](#testing)
6. [Open Questions](#open-questions)
7. [Non-Goals](#non-goals)

## Product Requirements

| As a… | I want… | So that… |
|---|---|---|
| Listener (phone) | to tap the bar and get a big scrubber and skip buttons | I can move around a long episode with my thumb |
| Listener | a speed control that remembers my choice | I listen at 1.5× without setting it every episode |
| Listener | to see where the people and topics I care about are mentioned, on the scrubber | I can jump to the part of the episode that matters (#28 §5.2's original intent) |
| Listener | to jump into the transcript at the current moment | I can read what I just heard without scrolling to find it |
| Listener (video) | to see the video in the expanded player when I have navigated away from the reader on a phone | phones have no floating tile (#61 §2) |
| Listener | to stop and dismiss the player from the sheet | the phone bar does not carry a destructive action next to Play |
| Keyboard user | Esc to close the sheet and arrow keys on the scrubber | the sheet is operable without a pointer |

## Design

### Entry and exit

- **Open:** tap/click the bar's artwork or title block (the whole left region
  is the target; the title stops being a link and becomes the expand
  affordance — the sheet carries the link to the episode instead). A chevron
  affordance at the left edge of the bar signals expandability on desktop.
- **Close:** Esc; tap the scrim (phone); click outside (desktop); the ✕ in
  the sheet header; swipe down on the drag handle (phone, pointer events,
  ≥ 80 px or velocity threshold). Closing never affects playback.
- Open state lives in `Layout` (the sheet and bar are siblings there); no
  route change, no history entry. A route change closes the sheet.

### Surfaces

| | Phone (< `sm`) | Desktop (≥ `sm`) |
|---|---|---|
| Form | bottom sheet, `max-h-[92vh]`, rounded top, drag handle, scrim | card `w-[26rem]` anchored above the bar's left edge (artwork side), shadow, no scrim |
| Rung | `z-[70]` (transient) | `z-[70]` |
| Focus | `role="dialog"`, `aria-modal="true"`, trap, restore to the bar on close | `role="dialog"`, no trap; Esc/outside-click close |
| Body scroll | locked while open | not locked |

### Contents, top to bottom

1. **Header** — artwork (96 px phone / 64 px desktop), episode title (link to
   the episode; keeps the #52 overlay contract exactly as the bar's link does
   in #71), podcast title, ✕ close.
   - *Video (phase 2c):* when the session's presentation would otherwise be
     `hidden` on a phone (no theater slot, no floating tile), the header
     registers a 16:9 slot and the media layer positions the stable `<video>`
     over it. Closing the sheet unregisters it; playback continues
     audio-first exactly as when leaving the reader today.
2. **Scrubber** — full width, 44 px hit area, `<input type="range">` with
   `aria-valuetext` as `m:ss of h:mm:ss`. Elapsed on the left; the right label
   toggles between total and remaining on tap (persisted). Disabled with
   `--:--` while duration is unknown.
   - *Entity ticks (phase 2b):* the top-5 entities by mention count (same
     set as the reader's strip) drawn as a row of dots beneath the track,
     coloured by `entityStyle(type)`. Hover / long-press shows the entity
     name and timestamp; tap seeks. This *is* the #28 §5.2 mention density
     timeline; the floating strip is removed.
3. **Transport** — back 15 · play/pause (56 px) · forward 15. Same handlers
   as the bar.
4. **Speed** — segmented control `0.8× · 1× · 1.2× · 1.5× · 2×`. Persisted
   in `localStorage` (`thestill:player:rate`) and applied on every
   `play()`/engine switch by `PlayerProvider`, so the choice survives
   reloads and rendition switches. On the YouTube engine the control shows
   only rates the iframe API accepts for the current video
   (`getAvailablePlaybackRates()`), greyed otherwise.
5. **Secondary actions** (icon + label chips, wrap on narrow widths):
   - **Open transcript here** → navigates to the episode with
     `?view=transcript&t=<seconds>`; the reader's existing deep-link seek
     (`useDeepLinkSeek`) scrolls to the segment. Carries `backgroundLocation`
     per the #52 contract.
   - **Follow playback** toggle → the #38 persisted auto-scroll boolean
     (`thestill:transcript:follow`), so it can be flipped without opening
     the transcript.
   - **Show video / Hide video** and **Picture-in-picture** → the existing
     `setVideoPreference` / `requestPip` from #61, shown only when a visual
     rendition exists.
   - **Volume** (desktop only) → slider bound to `volume` / `setVolume`;
     mute toggle.
6. **Stop** — text button, destructive styling, bottom of the sheet. Calls
   `stop()` and closes the sheet. The desktop bar keeps its ✕ as well.

### Keyboard

- Space: toggle (from #71, unchanged; the sheet's own buttons keep native
  space activation).
- ← / → while the scrubber has focus: native ±step; step set to 5 s.
- Esc: close.

### States

| State | Sheet behaviour |
|---|---|
| No track | Bar hidden; sheet cannot open |
| Loading / unknown duration | Scrubber disabled, ticks hidden, transport enabled |
| Media error | Error line under the header, same text as the theater surface |
| YouTube engine | Rate control constrained; Hide video switches to the audio rendition (#62 §7 policy unchanged) |
| Track changes while open | Sheet stays open and re-binds; entity ticks refetch |

## Architecture

- **New** `components/NowPlayingSheet.tsx` — the sheet/card. Reads
  `usePlayer()` / `usePlayerTime()` like the bar; owns no playback state.
- **New** `hooks/usePlayerRatePreference.ts` — persisted rate; applied inside
  `PlayerProvider` on track start and engine switch (the only
  `PlayerContext` change: apply a stored rate; expose
  `availableRates` for the YouTube engine).
- **Changed** `MiniPlayer.tsx` — artwork/title block becomes the expand
  button (`aria-expanded`); accepts `onExpand`. The episode link moves into
  the sheet.
- **Changed** `Layout.tsx` — holds `isNowPlayingOpen`; renders the sheet
  beside the bar; closes it on route change.
- **Changed** `EpisodeReader.tsx` / `MentionDensityTimeline.tsx` — the
  floating strip is removed once phase 2b lands; `MentionDensityTimeline`'s
  dot layout is reused as the tick row inside the scrubber (pure UI, already
  receives `entities` + `durationSeconds` + `onSeek`).
- **Data** — entities for the ticks come from the existing
  `useEpisodeEntities(podcastSlug, episodeSlug)` keyed off the track's slugs,
  sharing the reader's React Query cache entry. Duration from
  `player.duration`, falling back to `track.durationHint`.
- **Theater slot in the sheet (2c)** — `registerTheaterSlot(episodeId, el)`
  from #61 §3, exactly as `TheaterSurface` does. Phone-only; on desktop the
  floating tile already covers the off-reader case.

No backend or API changes.

## Implementation Phases

| Phase | Scope | Gate |
|---|---|---|
| 2a | Sheet + card chrome (open/close/focus/scrim/drag), header, scrubber (no ticks), transport, speed with persistence, Stop, volume (desktop) | Playwright at 390 and 1440: open from the bar, seek, change speed, Esc/scrim/swipe close; unit tests for rate persistence and engine constraints |
| 2b | Entity ticks on the scrubber; Open transcript here; Follow playback toggle; remove the floating `MentionDensityTimeline` strip and #71's overlay suppression | Ticks match the reader strip's top-5; tap seeks + deep-link scroll lands on the segment |
| 2c | Video slot in the sheet header on phones; PiP / Show video chips | Existing #61 continuity tests extended: sheet open/close never restarts playback; iframe policy (#62) holds |

Each phase is independently landable.

## Testing

- Unit (vitest): open/close paths; focus trap and restore on phone; rate
  applied on `play()` and after `switchRendition`; YouTube rate constraint;
  Stop closes and clears; ticks derived from the same top-5 as the strip;
  "Open transcript here" builds the URL with the current time and the
  background location.
- Playwright (new, first browser suite for the player): phone and desktop
  flows above, plus the #71 case now closable end-to-end — reader open, bar
  visible, expand sheet, seek, close, reader still where it was.

## Open Questions

1. **Desktop form: anchored card vs. right-hand Now Playing panel** (Spotify's
   sidebar). Card proposed: it costs no layout and the reader already owns
   the right edge at `lg`. Revisit if the entity ticks want more width than
   26 rem gives them.
2. **Rate scope** — global (proposed) or per podcast? Pocket Casts offers
   per-podcast effects; Apple Podcasts is global. Start global; a per-podcast
   override is an additive `localStorage` key later.
3. **Should the desktop bar keep its ✕** once Stop lives in the sheet? Kept
   for now; remove if analytics show no use.
4. **Sleep timer** — a natural sixth section but no requirement yet.

## Non-Goals

- Queue / up next (still #22's non-goal).
- Chapter markers — no chapter data is ingested; the tick row is designed so
  chapters can be a second series later.
- Cross-device playback position sync.
- Any change to karaoke rendering or the transcript viewer beyond the
  deep-link entry.

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-03 | Drafted from the player/overlay design review. Sheet is transient (`z-[70]`) rather than a fourth long-lived surface. Entity timeline relocates onto the scrubber instead of being repositioned as a floating strip. Rate persistence added to the provider rather than to the sheet so it survives engine switches. |
