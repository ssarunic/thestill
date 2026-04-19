# Floating Media Player — Persistent Playback Across Routes

**Status**: 🚧 Active development (shipped on branch `claude/floating-media-player-Xomlj`, PR #3)
**Created**: 2026-04-19
**Updated**: 2026-04-19
**Priority**: Medium (UX quality-of-life; unblocks richer player work in spec #18)

## Overview

The web UI's audio player lives inside the episode-details page. Navigating
away from that page unmounts the player component, which tears down the
underlying `<audio>` element and stops playback. This is contrary to how users
expect a media player to behave — Spotify, Apple Podcasts, Pocket Casts, and
YouTube Music all keep playback alive while the user browses other screens.

This spec plans a persistent player: once a user starts an episode, playback
continues uninterrupted as they move around the app, and a compact "mini
player" control surface is visible on every page so they can pause, seek, and
see what's playing.

The design is also chosen with spec #18 in mind — the planned
segment-preserving transcript work enables richer playback UX (synced
transcript highlighting, chapter navigation, per-segment jump). This spec sets
up the architectural seam we need for that future custom player without
committing to it now.

## Goals

1. Audio playback survives client-side route changes.
2. A mini player is visible on every authenticated page whenever an episode is
   loaded; it exposes at minimum play/pause, current time, duration, scrub,
   and a "go to episode" affordance.
3. The episode-details page retains a richer inline player view that stays in
   sync with the mini player (both read/write the same state).
4. The abstraction cleanly supports swapping the native `<audio>` element for
   a custom player (Howler / WaveSurfer / canvas-based) later, without
   touching consumer components.
5. Small blast radius for the initial implementation — one new context, one
   new UI component, one touched page.

## Non-goals

- Background playback when the browser tab is closed or backgrounded beyond
  what the browser provides natively.
- A playback queue / up-next list. Designed to be added later on top of the
  context, but not part of this spec.
- Media Session API integration (lock-screen controls, hardware media keys).
  Easy follow-up once the context exists; not required for v1.
- Persisting playback position across page reloads / devices. Follow-up.
- Server-side streaming, HLS, or DRM. We keep using the existing
  `episode.audio_url`.
- The richer custom player itself (waveform, synced transcript, chapter
  markers). Lives in spec #18 and a future player spec.

## Background

### Current frontend stack

- React 18.3 + React Router 6.28 + TanStack React Query 5.62
  ([main.tsx](../thestill/web/frontend/src/main.tsx),
  [App.tsx](../thestill/web/frontend/src/App.tsx)).
- State management is Context API only —
  [AuthContext](../thestill/web/frontend/src/contexts/AuthContext.tsx) and
  `ToastProvider`. No Redux / Zustand.
- All authenticated routes are wrapped in
  [Layout.tsx](../thestill/web/frontend/src/components/Layout.tsx), which
  renders the sidebar / header and an `<Outlet />` at line 242. Layout stays
  mounted across route changes.

### Current player

[AudioPlayer.tsx](../thestill/web/frontend/src/components/AudioPlayer.tsx) is a
stateless wrapper around a native `<audio controls>` element. It is only
mounted from
[EpisodeDetail.tsx:220](../thestill/web/frontend/src/pages/EpisodeDetail.tsx#L220).
Because it lives inside the route subtree, React Router unmounts it on
navigation and the browser tears down the audio element.

### Why this is the right moment to fix it

- Spec #18 (segment-preserving transcript cleaning) is setting up the data
  model (word-level timestamps) for synced playback UX. A central player
  context is the consumer-side half of that work.
- Any future move to a custom player (canvas waveform, chapter UI, speed
  ramping, gapless transitions) needs a stable state interface between the
  player engine and the rest of the UI. Introducing that interface now, while
  the audio element is still a thin wrapper, is cheap; doing it later as part
  of the custom-player work conflates two changes.

## Options considered

### Option A — PlayerContext + single persistent `<audio>` in `Layout`  *(chosen)*

A `PlayerProvider` rendered inside `Layout` owns exactly one long-lived
`<audio>` element via a ref. It exposes state and actions through context:

```ts
type PlayerState = {
  currentEpisode: Episode | null
  isPlaying: boolean
  currentTime: number
  duration: number
  buffered: number
  playbackRate: number
  play: (episode: Episode) => void
  pause: () => void
  resume: () => void
  toggle: () => void
  seek: (seconds: number) => void
  setRate: (rate: number) => void
  stop: () => void
}
```

A floating `<MiniPlayer />` renders inside `Layout` whenever
`currentEpisode` is set. `EpisodeDetail` stops rendering its own `<audio>` and
instead calls `player.play(episode)`; a new inline "full player" view reads
the same context so the two stay in sync trivially.

#### Pros

- Persistence is free: the audio element is mounted once above `<Outlet />`
  and never touched on navigation.
- Matches existing patterns (Context is already how `AuthContext` /
  `ToastProvider` work). No new dependency.
- The context is the abstraction seam. Swapping native `<audio>` for Howler /
  WaveSurfer / a custom canvas player is an internal change to the provider;
  consumers (`MiniPlayer`, episode full-view, future queue UI, future
  transcript-synced highlighting) don't change.
- Testable: the provider can be mocked for component tests.
- Small diff: one new file (context+provider+mini-player can live together),
  one touched page, one deletion path for the current `AudioPlayer`.

#### Cons

- Context value changes on every `timeupdate` event (~4 Hz) can re-render
  every consumer. Mitigations: split high-frequency state (`currentTime`,
  `buffered`) into its own context; memoize the static action surface;
  consumers that only need actions subscribe to a stable
  `PlayerActionsContext`.

### Option B — React Portal to `document.body`

Render the `<audio>` element via `createPortal` so the node lives outside the
route subtree.

#### Pros

- Solves the unmount problem without introducing context.

#### Cons

- Doesn't solve state sharing. A mini player on every page and a rich view on
  the episode page both need to read the same state, so we still need a
  context / store.
- The portal is pure extra complexity on top of what Option A needs anyway.
- No abstraction seam for the future custom player.

**Verdict:** rejected. Portals don't carry their weight here.

### Option C — Module-level singleton audio service + `usePlayer()` hook

A plain TypeScript module holds the `HTMLAudioElement` (or Howler instance)
and exposes a pub/sub API; React components subscribe through a hook.

#### Pros

- Fully decoupled from the React tree.
- Easy to drive from non-React code (e.g., a hotkey handler, a service
  worker).

#### Cons

- Functionally equivalent to Option A but bypasses React's ownership model.
- StrictMode double-invocation and HMR need manual guards to avoid creating
  two audio elements.
- No SSR story if we ever want one.
- More moving parts than the problem currently warrants.

**Verdict:** rejected today. If we later need to drive the player from
non-React surfaces (MCP, extension, service worker), we can lift the engine
into a module and keep the context as a thin React adapter — that refactor is
cheap from Option A.

### Option D — Third-party player library (react-h5-audio-player, Plyr, etc.)

Drop in a packaged player component.

#### Pros

- Fast to ship.

#### Cons

- Still needs to be mounted in `Layout` with shared state, i.e. still Option
  A underneath.
- Locks UI to the library's styling and component surface, which fights the
  future custom-player direction.
- Adds a dependency for something we'll outgrow.

**Verdict:** rejected. Doesn't save work and constrains the future.

## Decision

**Option A.** A `PlayerProvider` living in `Layout` owns a single persistent
`<audio>` element and exposes a context surface. A floating `MiniPlayer`
renders in `Layout`; `EpisodeDetail` uses the same context for its richer
inline view.

Rationale recap:

1. It actually solves the stated problem (persistence).
2. It matches the codebase's existing state-management idioms.
3. It gives us the seam the future custom player needs without committing to
   a specific engine today.
4. Minimal blast radius — one provider, one mini-player component, one
   touched page, one retired component.

## Implementation plan

### Phase 1 — Context + persistent audio element

- New file:
  `thestill/web/frontend/src/contexts/PlayerContext.tsx`.
  Exports `PlayerProvider`, `usePlayer()`, and (optional split) a
  `PlayerActionsContext` for subscribers that don't need high-frequency
  time updates.
- Provider owns `audioRef = useRef(new Audio())`, wires listeners for
  `play`, `pause`, `timeupdate`, `durationchange`, `ended`, `ratechange`,
  `progress`, `error`, and reflects them into state.
- Actions: `play(episode)` sets `audioRef.current.src` to
  `episode.audio_url` and calls `.play()`. Subsequent `play(sameEpisode)`
  resumes; `play(differentEpisode)` swaps source and starts at 0.
- Mount `PlayerProvider` inside `Layout` so it wraps `<Outlet />`. The audio
  element itself is rendered as a hidden child of the provider.

### Phase 2 — Mini player UI

- New component:
  `thestill/web/frontend/src/components/MiniPlayer.tsx`.
- Renders nothing when `currentEpisode` is null. Otherwise renders a fixed
  bar at the bottom of the viewport above the main content with: episode
  title, podcast name, play/pause, current-time/duration, scrubber, close
  button, and a link to the episode-details page.
- Rendered in `Layout` as a sibling of `<main>` so it floats above content
  on every authenticated route.
- Responsive: collapses to essentials on mobile; full controls on
  tablet/desktop. Respects the sidebar width already used by `<main>`.

### Phase 3 — Wire `EpisodeDetail` to the context

- Replace the `<AudioPlayer audioUrl={…} title={…} />` usage at
  [EpisodeDetail.tsx:220](../thestill/web/frontend/src/pages/EpisodeDetail.tsx#L220)
  with a richer inline view that reads from `usePlayer()` and calls
  `player.play(episode)` from its play button.
- Decide the fate of the existing `AudioPlayer` component: either retire
  it, or repurpose it as the full-view consumer of the context. Pick one in
  the PR.

### Phase 4 — QA + polish

- Keyboard: space toggles play/pause when the mini player is focused; arrow
  keys scrub ±5s.
- Accessibility: proper ARIA roles (`role="region"`,
  `aria-label="Audio player"`), labeled buttons, screen-reader time
  announcements.
- Analytics / logging hook point: emit a structured event on
  `play`/`pause`/`ended` so we can later measure engagement. Not wired to a
  backend in this spec.

### Deferred (explicitly out of scope for v1)

- Media Session API (lock-screen / hardware-key controls).
- Persisting playback position across reloads (localStorage / server).
- Playback queue / auto-advance to next episode.
- Synced transcript highlighting (depends on spec #18 segment timestamps).
- Waveform / chapter UI (future custom-player spec).
- Background playback in a service worker.

## Open questions

- **Single tab vs multi-tab.** If a user opens two tabs and plays in both,
  both `<audio>` elements play concurrently. Match Spotify-web behavior
  (pause other tabs via `BroadcastChannel` / `storage` event) or accept the
  double playback? Lean toward accepting it in v1 and revisiting if users
  complain.
- **Render perf.** Start with a single context and measure. If profiler
  shows route-level re-renders on `timeupdate`, split into
  `PlayerStateContext` (low-frequency: episode, isPlaying, duration) and
  `PlayerTimeContext` (high-frequency: currentTime, buffered), and have the
  scrubber subscribe only to the latter.
- **Close button semantics.** Does closing the mini player just pause, or
  does it also clear `currentEpisode` and unload the source? Default: clear
  and unload, matching Spotify's behavior when you dismiss the "Now
  Playing" bar.

## References

- [Layout.tsx](../thestill/web/frontend/src/components/Layout.tsx) — host of
  the new provider and mini player.
- [EpisodeDetail.tsx:220](../thestill/web/frontend/src/pages/EpisodeDetail.tsx#L220)
  — current integration point that will switch to `usePlayer()`.
- [AudioPlayer.tsx](../thestill/web/frontend/src/components/AudioPlayer.tsx)
  — component retired or repurposed by this spec.
- Spec #18 [segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md)
  — data-model prerequisite for the future synced-transcript UX that this
  player's abstraction seam is designed to support.
