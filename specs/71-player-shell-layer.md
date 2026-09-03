# Player Shell Layer — Mini Player Above the Reader

**Status**: ✅ Implemented on `feat/71-player-shell-layer` (2026-09-03); pending merge
**Created**: 2026-09-03
**Updated**: 2026-09-03
**Priority**: High (the only transport in the app is unusable while reading)

> **Related:** [#22 floating-media-player](22-floating-media-player.md) (owns the mini player), [#52 inbox-reader-overlay](52-inbox-reader-overlay.md) (open question 5 is this spec), [#61 unified-av-playback-session](61-unified-av-playback-session.md) (floating tile, media layer z-order), [#28 corpus-search-and-entities](28-corpus-search-and-entities.md) §5.2 (mention density timeline), [#72 now-playing-sheet](72-now-playing-sheet.md) (the follow-up this spec makes room for)

## Overview

The mini player (`fixed bottom-0`, `z-30`) is the app's only transport. The
inbox reader overlay (`fixed inset-0`, `z-50`) is drawn over it. Because the
overlay panel is right-aligned at `lg:max-w-4xl` and every control in the bar
is right-aligned too, the controls are covered on desktop at *any* window
width; only the artwork/title sliver peeks out under the scrim, where a click
closes the reader. Below `lg` the panel is full width and the bar vanishes
entirely. The reader itself carries one Play button at the top of a page that
can be thousands of words long, so once the transcript scrolls nothing can
pause playback.

The root cause is a category error: the reader is a long-lived reading
surface built with transient-modal semantics (same rung as ⌘K and the add
dialog). Spec #52's open question 5 accepted this for v1 ("revisit if it
annoys"). It does.

This spec records the rule that fixes it and the changes that implement it.
The design review behind it (competitor survey, per-breakpoint geometry,
options considered) is summarised in [Alternatives](#alternatives-considered).

## The rule

**The mini player is shell chrome — the bottom edge of the app, as the
sidebar is the left edge. Long-lived surfaces lay out *inside* the shell.
Only transient surfaces may cover it.**

- Long-lived: pages, the reader overlay. They inset above the player.
- Transient: ⌘K, add/import dialogs, failure details, the mobile nav drawer,
  toasts, the tablet expanded-sidebar. They sit above the player and are
  dismissed in seconds.

This is the model Spotify desktop, YouTube Music web and SoundCloud use (a
fixed player layer all content insets from), and the mobile equivalent of
Apple's tab-bar accessory (pushed pages keep the player; modal sheets cover
it). Material's bottom-app-bar guidance says it directly: pad content by the
bar's height rather than overlap it.

### Layering ladder

Every fixed surface picks a rung. Recorded in
[constants/layers.ts](../thestill/web/frontend/src/constants/layers.ts).

| Rung | Surfaces | Before |
|---|---|---|
| `z-0..20` | page content, sticky rails, in-panel floating pills | unchanged |
| `z-40` | sidebar, mobile header, floating video tile, bulk-actions bar | unchanged |
| `z-[45]` | reader overlay (scrim + panel) | was `z-50` |
| `z-50` | **mini player** | was `z-30` |
| `z-[52]` / `z-[55]` | tablet sidebar scrim / expanded sidebar (never coexists with the reader — its hamburger sits under the reader's scrim) | was `z-30` / `z-40` |
| `60` | media layer when its slot is inside the reader (`PlayerContext`) | unchanged |
| `z-[70]` | ⌘K, add podcast, import, failure details, nav drawer, toasts | was `z-50` / `z-40` |

### The height variable

The bar publishes its rendered height as `--player-h` on `<html>`
(`ResizeObserver`; `0px` when nothing is loaded or on unmount). Everything
bottom-anchored reads it through `abovePlayer(gap)` instead of hard-coding
the bar's size:

| Surface | Before | After |
|---|---|---|
| `<main>` padding | `pb-24` when a track exists | `padding-bottom: var(--player-h)` |
| Reader overlay wrapper | `inset-0` | `inset-x-0 top-0`, `bottom: var(--player-h)` |
| Floating video tile | `bottom-24` | `calc(var(--player-h) + 1rem)` |
| "Resume follow" pill | `bottom-28` | `calc(var(--player-h) + 1rem)` |
| Mention density timeline | `bottom-20` | `calc(var(--player-h) + 0.5rem)`; not rendered inside the overlay |
| Bulk-actions bar | `bottom-0` (fought the player for the edge) | `bottom: var(--player-h)` — stacks above it |
| Toasts | `bottom-4` | `calc(var(--player-h) + 1rem)` |

Inline `style` rather than Tailwind's `bottom-(--player-h)` so the `0px`
fallback is explicit and the value is assertable in jsdom.

## Mini player changes

1. **Transport on phones.** Back 15 / forward 15 were `hidden sm:flex`; the
   phone bar showed play/pause and Close only. Now both skips render at every
   size (44 px targets below `sm`), artwork shows at every size, and Close —
   stop-and-dismiss, destructive next to Play — is hidden below `sm`. It
   moves into the expanded sheet in #72. The time readout stays desktop-only.
2. **Space toggles playback** whenever a track is loaded, unless the event
   target is an input, textarea, select, button, contenteditable, slider or
   iframe. Mirrors the Media Session handlers for the keyboard.
3. **Title / "Show video" links keep the overlay contract.** From `/inbox`,
   or from inside an overlay already open over it, the link carries
   `backgroundLocation` so the reader opens above the still-mounted list.
   Elsewhere it is a plain navigation, as before. Clicking while already on
   the episode is a no-op instead of a duplicate history entry.
4. **Safe area.** `pb-[env(safe-area-inset-bottom)]` on the bar and
   `viewport-fit=cover` in `index.html` so the home indicator no longer sits
   on the play button in standalone Safari.

## Testing

- `MiniPlayer.test.tsx` (new): height variable published/reset; rung class;
  space toggles except in a text field; skips visible and Close hidden on
  phones; title link carries `backgroundLocation` from `/inbox` only; no
  duplicate push when already on the episode.
- `EpisodeReaderOverlay.test.tsx`: wrapper has `bottom: var(--player-h, 0px)`
  and the `z-[45]` rung, no `inset-0`.
- Full frontend suite green (342 tests). `tsc` clean. ESLint: the eight
  `react-hooks/set-state-in-effect` errors in touched files are identical to
  main (pre-existing, not in changed lines).
- Browser pass (2026-09-03, Playwright against a single-user scratch server
  on a copy of `data/podcasts.db`, real audio playing from the reader):
  - 1440×900: overlay bottom 835 = bar top 835, `--player-h` 65px; the
    bar's Pause and title link are the topmost elements at their own
    positions; no horizontal scroll.
  - 768×1024 (full-width panel): overlay bottom 959 = bar top; skips and
    Close visible. Expanded tablet sidebar sits at z-55 over the z-52
    scrim; its user menu is clickable where it overlaps the bar, and the
    bar is under the scrim as designed.
  - 390×844: overlay bottom 783 = bar top, bar 61px; back/play/forward
    all 44×44, Close hidden, artwork visible, 178px left for the title.
  - Not exercised: the bulk-actions bar (the scratch DB predates the
    current episodes schema, so the list 500s and nothing is selectable).
    It uses the same `abovePlayer()` offset as the overlay.

## Alternatives considered

- **Transport inside the reader header** — only fixes the same-episode case;
  a second transport once the bar is visible; fights the header at 390 px.
- **Reader as an in-flow route panel** (spec #52 alternative 3) — cleanest,
  removes the modal semantics entirely, but reopens focus/scroll/Esc/mobile
  contracts. Not blocked by this spec: the CSS variable survives the move.
- **Raise only the player, leave the overlay full height** — the bar would
  float over the panel's bottom edge; the panel's own scroll container would
  hide its last lines behind it (Spotify mobile's long-standing bug).

## Non-goals

- The expanded Now Playing surface, speed/volume UI, scrubber touch target,
  entity ticks — all #72.
- Any change to playback engines or `PlayerContext` transport.

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-03 | Design review of the player/overlay collision. Spotify/YouTube Music shell model adopted over in-reader transport or split-pane rebuild. Implemented as one branch with the layering ladder recorded in `constants/layers.ts`; transient modals bumped to `z-[70]` rather than lowering the player below them. Mention-density timeline suppressed inside the overlay pending #72 rather than repositioned (it would paint across the panel's bottom edge in the dialog's stacking context). |
