# Transcript Playback Sync — Highlight, Auto-scroll, Click-to-seek

**Status**: ✅ Complete
**Created**: 2026-04-19
**Updated**: 2026-04-20
**Priority**: Medium (UX quality-of-life, builds on specs #18 and #22)

## Overview

Now that most episodes have segmented transcripts (spec #18) and the persistent
floating media player is in place (spec #22), the segmented transcript view can
become a synchronous reading surface for the audio: the currently-playing
segment is highlighted, the page optionally follows the playhead, and clicking
a segment seeks the player. A handful of related UX affordances (deep-linked
timestamps, in-transcript search, filler-segment reveal, keyboard navigation)
round out the experience.

All work is frontend-only. Data is already present on
[AnnotatedSegment](../thestill/web/frontend/src/api/types.ts#L215)
(`start`, `end`, `speaker`, `text`), and `PlayerContext` already exposes a
low-frequency `usePlayer()` state context and a high-frequency
`usePlayerTime()` tick context
([PlayerContext.tsx](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L40)).

## Goals

1. Visual feedback of the currently-playing segment while audio plays.
2. Optional "follow playback" mode that scrolls the active segment into view.
3. Click / keyboard-activate any segment to seek the player to that point.
4. Deep links like `?t=754` (and rich timestamp badges) so shared URLs land on
   a specific moment.
5. Optional filler-segment reveal so users can audit what the pipeline skipped.
6. In-transcript search with match highlighting.
7. Keyboard shortcuts for power users; virtualisation if render cost warrants.

## Non-goals

- Word-level highlighting (would require word-level timestamps on the client).
- Persisting follow-mode state server-side (client-only `localStorage`).
- Adjusting transcript data on the backend — this spec touches only the web
  frontend.
- Reworking the legacy (non-segmented) transcript view.

## Background

### Current shape

- `SegmentedTranscriptViewer`
  ([SegmentedTranscriptViewer.tsx](../thestill/web/frontend/src/components/SegmentedTranscriptViewer.tsx))
  renders `AnnotatedSegment[]`, filtering out `kind === 'filler'` segments.
  Each visible segment shows a speaker name, a timestamp, and body text; ad
  breaks render as an amber-accented callout.
- `PlayerContext` owns a single `<audio>` element and publishes
  `currentTime` through a dedicated context so ticks don't rerender player
  consumers. `seek(seconds)` is already available; `play(track)` accepts a
  `PlayerTrack` describing an episode.
- The speaker palette lives in
  [utils/speakerColors.ts](../thestill/web/frontend/src/utils/speakerColors.ts);
  it returns Tailwind `text-*-700` classes keyed by speaker name.
- A toast API is available via `useToast()`
  ([Toast.tsx](../thestill/web/frontend/src/components/Toast.tsx)). Clipboard
  copy with toast feedback is already used by `ShareButton`.

## Design

### Shared utilities

Extend `utils/speakerColors.ts` with `getSpeakerBorderColor(speaker)` that
mirrors the existing text-colour map and returns `border-*-500` classes so
active-segment accents stay colour-consistent with the speaker label.

Add a small `utils/transcriptSearch.ts` helper for binary-searching segments
by playback time, returning the active segment index (or `-1`). Accepts
`playback_time_offset_seconds` as an offset parameter so the caller stays
unaware of the offset mechanics.

### Component contract

```ts
interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
  episodeId?: string | null      // enables active-segment highlighting for this episode
  onSeekRequest?: (seconds: number) => void
  initialSeekSeconds?: number    // for ?t= deep-link hydration
  // future: followPlayback, onFollowChange, searchQuery
}
```

`EpisodeDetail` owns the click handler: if the active player track already
matches `episode.id`, it calls `player.seek()`; otherwise it calls
`player.play({...})` with the full `PlayerTrack`. The viewer stays a dumb
presentation component, which keeps it reusable and testable.

### PR roadmap

Each PR lands independently behind the existing "Segmented" sub-tab and is
revertable on its own.

| PR | Scope | New files | Touched files |
|---:|---|---|---|
| 1 | Highlight current segment | `utils/transcriptSearch.ts` | `speakerColors.ts`, `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 2 | Click / keyboard to seek | — | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 3 | Follow-playback toggle | `hooks/useAutoScrollFollow.ts` | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 4 | Deep links, filler reveal, search | `hooks/useDeepLinkSeek.ts` | `SegmentedTranscriptViewer.tsx`, `EpisodeDetail.tsx` |
| 5 | Keyboard nav, virtualisation decision | — | `SegmentedTranscriptViewer.tsx` |

### PR 1 — Highlight current segment

1. Add `getSpeakerBorderColor` to `speakerColors.ts`.
2. Implement `findActiveSegmentIndex(segments, currentTime, offset, tolerance)`
   in `utils/transcriptSearch.ts` as an O(log n) binary search with a "latest
   segment whose `start + offset ≤ currentTime`" fallback so highlights don't
   flicker in micro-gaps. Clamp to the visible span using
   `segments[idx].end + offset + tolerance >= currentTime`.
3. In `SegmentedTranscriptViewer`, call `usePlayerTime()` + `usePlayer()` once
   at the top, derive `activeId` with `useMemo([currentTime, segments, offset,
   track?.episodeId, episodeId])`, and only treat a segment as active when the
   player track matches `episodeId`.
4. Pass `isActive: boolean` to `ContentSegment` / `AdBreak`. Wrap each in
   `React.memo` to avoid re-rendering non-active segments on every tick.
5. Active style: bg tint (`bg-primary-50/60`), left accent switched to the
   speaker's border colour, timestamp darkened to `text-primary-700`. Respect
   `prefers-reduced-motion` — no pulse, just static emphasis.

### PR 2 — Click / keyboard seek

1. Add `onSeekRequest?: (seconds: number) => void` prop.
2. In segment markup, add `role="button"`, `tabIndex={0}`, a click handler
   that calls `onSeekRequest(segment.start + offset)`, and a keyboard handler
   for Enter/Space. Add `focus-visible:ring-2 ring-primary-400 outline-none`
   and `cursor-pointer` on hover.
3. In `EpisodeDetail`, build the handler inline, using `player.isCurrent()`
   to decide between `seek()` and `play({...PlayerTrack})`. Preserve
   play/pause state on seek so browsing paused transcripts doesn't suddenly
   start audio.

### PR 3 — Follow-playback toggle

1. New `hooks/useAutoScrollFollow.ts` encapsulates: a `Map<id, HTMLElement>`
   ref registry, an effect that scrolls the active node into view when
   follow-mode is on, and user-scroll detection that temporarily suspends
   follow-mode for ~8 seconds (with a "Resume follow" pill). Use
   `scrollIntoView({ block: 'center', behavior: 'smooth' })` honoring
   `prefers-reduced-motion` (fall back to `'auto'`).
2. Persist the toggle state in `localStorage` under
   `thestill:transcript:followPlayback`.
3. Toggle chip lives in `EpisodeDetail`, next to the sub-tab switcher.
4. Default **off** — auto-hijacking scroll surprises users.

### PR 4 — Deep links, filler reveal, search

1. **Deep links.** Timestamp badge becomes a button; clicking copies
   `${location.origin}${location.pathname}?t=<seconds>` to the clipboard via
   `useToast()`. On mount, `useDeepLinkSeek` reads `?t=` and fires
   `onSeekRequest` exactly once after the viewer has its segments.
2. **Filler reveal.** New `showFiller` toggle (local state, persisted under
   `thestill:transcript:showFiller`) that un-filters filler segments and
   renders them muted (`text-gray-400 italic`).
3. **Search.** Sticky input above the list. Debounced 150ms. Matching text
   is wrapped in `<mark class="bg-yellow-100 rounded px-0.5">`; non-matching
   segments dim to `opacity-40` but remain in the list so context is
   preserved. Jumping to "next / previous match" keys off the search results
   array.

### PR 5 — Keyboard nav, virtualisation

1. **Keyboard**: `J`/`K` (or `↓`/`↑`) jump to next / previous segment and
   scroll it into view; `Enter` seeks to it; `F` toggles follow mode; `/`
   focuses search; `?` opens a keybindings sheet. Only active when the
   transcript panel is focused (don't hijack page-wide typing).
2. **Virtualisation**: defer unless profiling with the longest-running shows
   (3-hour episodes) reveals a frame-time regression. If needed, use
   `@tanstack/react-virtual` (pulls in cleanly alongside React Query).

## Performance considerations

- Put `usePlayerTime()` high in the viewer so segments themselves never
  subscribe to the tick. `React.memo` on segment rows with `isActive` as
  the only frequently-changing prop keeps tick cost O(1) in terms of DOM
  diffing.
- `useMemo` the active index so the search only runs once per parent
  render.
- Auto-scroll runs on *active-id change*, not on every tick — it's cheap.

## Accessibility

- Segment buttons expose `aria-label` including speaker + timestamp.
- Active segment announces via an `aria-live="polite"` region throttled to
  speaker changes, not every segment boundary.
- All colour-coded state has a non-colour signal (bold border, background
  shift).
- Keyboard parity with mouse for seek.

## Testing

- Unit: `findActiveSegmentIndex` with edge cases (before-first, after-last,
  in-gap, exact-boundary, filler-only region).
- Component: render with a fixed segment list + mocked player time context;
  assert the right segment has `data-active="true"` and receives focus ring
  after click.
- Type-check and lint pass on every PR.
- Manual smoke in Chromium via the dev server.

## Open questions

- Should the mobile view embed the follow toggle in the mini-player chrome
  instead of above the transcript list? Deferred until PR 3 ships and we can
  feel the ergonomics.
- Where do we draw the line before introducing virtualisation? We'll record
  profiling numbers in PR 5 to decide.
