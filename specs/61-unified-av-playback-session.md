# Unified Audio/Video Playback Session

> **Status:** 🚧 In progress (2026-07-22, post-review v2 — portal reparenting rejected, presentation state machine + playback-asset manifest adopted).
> Implemented: Increment 1 (Media Session, stable `<video>` media layer, source-URL-compared resume); Increment 2 manifest + theater surface + RSS video-enclosure ingestion; Increment 3 floating tile, PiP button, hide-video toggle, offset-adjusted rendition switching, continuity regression tests.
> Deferred: local range-serving route + S3 presigned manifest URLs (no locally-retained video renditions exist yet — manifests carry publisher enclosure URLs), YouTube iframe engine (gated on the product/legal decision, §6), `captionsUrl` production (open item).
> **Created:** 2026-07-22
> **Author:** Engineering (playback design)
> **Related:** [#22 floating-media-player](22-floating-media-player.md) (owns `PlayerContext` + mini player — this spec is the "seam for future custom player" it promised), [#38 karaoke-word-highlighting](38-karaoke-word-highlighting.md) (consumes `getCurrentTime()` at rAF cadence — a hard constraint on any engine design), [#39 video-alternate-enclosure-player](39-video-alternate-enclosure-player.md) (its "unification with `PlayerContext`" non-goal is exactly this spec; #39 may still ship first as an interim step and its variant-selection logic feeds §5), [#23 transcript-playback-sync](23-transcript-playback-sync.md), [#18 segment-preserving-transcript-cleaning](18-segment-preserving-transcript-cleaning.md), [#34 briefing-audio-and-feeds](34-briefing-audio-and-feeds.md) (presigned-URL delivery deferred there lands here), [#35 pluggable-file-storage](35-pluggable-file-storage.md)

---

## Executive summary

Thestill's playback is audio-only: one hidden `<audio>` element owned by
`PlayerProvider` ([PlayerContext.tsx](../thestill/web/frontend/src/contexts/PlayerContext.tsx)),
a bottom-docked mini player, and transcript sync driven by a synchronous
`getCurrentTime()`. Video episodes are coming (YouTube-sourced feeds, RSS
video enclosures). This spec defines how video joins playback **without
breaking the product's core loop** — persistent playback across navigation
with word-synced transcripts.

The design was reviewed and revised; the review's corrections are folded in.
Two candidate approaches were explicitly rejected (§9). The result converges
on the model Spotify uses for video podcasts (§10): **one episode, multiple
renditions, one logical playback session; video is a presentation of the
session, not a different content type.**

## 1. Invariants

1. **One logical playback session, exactly one active playback engine.**
   Never two media elements with independent transport state. Most episodes
   use the native `HTMLMediaElement` engine; provider-specific engines (e.g.
   a YouTube iframe engine, §6) plug in behind the same session interface.
2. **One stable media DOM node.** The native engine's `<video>` element is
   created once in a global media layer and **never reparented**. React
   portals with changing targets remount their children, and moving a
   `<video>` in the DOM pauses playback per the HTML spec (`moveBefore()`
   fixes this but is Chromium-only). Presentation surfaces *register a
   rectangle*; the media layer positions the stable node over the active
   slot. React renders only shells and controls.
3. **Presentation is modeled separately from playback** (§3). Which surface
   shows the video is a UI state machine; play/pause/position/rate belong to
   the session and are never affected by surface changes.
4. **Rendition switches preserve logical position.** Switching video ↔ audio
   rendition (or engine) carries `position`, `playbackRate`, and play state
   across the source transition, adjusted by per-asset `timelineOffset`
   (§4, §7).
5. **Browser state is authoritative for native features.** PiP, fullscreen,
   and autoplay outcomes are read from events
   (`enterpictureinpicture` / `leavepictureinpicture`, etc.), never assumed.

Note on element reuse: `HTMLVideoElement` and `HTMLAudioElement` both inherit
the `HTMLMediaElement` API (video is not a strict superset of audio, but a
`<video>` element plays audio resources fine). The existing `PlayerContext`
transport API therefore carries over, but it does **not** stay literally
untouched — poster, captions, media error state, buffering, fullscreen,
volume, and accessibility are in scope (§8).

## 2. Presentation surfaces

### Web / desktop

- **Theater** — for video episodes, `EpisodeReader` registers a 16:9 slot
  above the transcript; the media layer positions the video over it.
  Karaoke transcript runs beneath exactly as today; clicking a word seeks.
- **Floating tile** — when no theater slot is registered (user navigated
  away) and video is enabled, a draggable ~320 px tile bottom-right, above
  the mini-player bar, with close (drops to audio-first) and expand (back to
  the episode) affordances.
- **Native PiP** — user-initiated via an explicit button. Progressive
  enhancement only: `requestPictureInPicture()` needs transient user
  activation and can reject; support is not universal.
- **Mini player** — unchanged for audio. For video it keeps **episode
  artwork / poster** (no live thumbnail — one DOM video cannot render in two
  places, and canvas snapshots cost CORS/refresh/power for a 40×40 payoff)
  and gains a "Show video" affordance. If video is presented nowhere, an
  expanded mini player may host the video itself.

### Mobile (responsive web)

- Theater sits sticky at the top of the episode page and collapses on
  scroll (YouTube-mobile pattern); the transcript stays the primary surface.
- Leaving the page: **no in-page floating tile.** Playback continues
  audio-first in the mini-player bar. Native PiP (iOS auto-PiP, Android PiP)
  is opt-in enhancement where the browser offers it — never relied on as
  policy.
- Media Session API integration (lock-screen artwork, play/pause/±15 s)
  ships regardless of video and benefits audio episodes. Every action
  handler is feature-detected and wrapped in `try/catch`
  (`setActionHandler` support varies).

## 3. Presentation state machine

```ts
mediaKind: 'audio' | 'video'
presentation: 'hidden' | 'theater' | 'floating' | 'native-pip'
videoPreference: 'shown' | 'audio-only'
```

Surface priority (highest wins):

1. `native-pip` while the browser reports it active.
2. `theater` when a compatible reader slot is registered.
3. `floating` (desktop) when off-reader and `videoPreference === 'shown'`.
4. `hidden` (audio-first) otherwise.

Slot presence is determined by **mounted surface registration, not
pathname**: `EpisodeReader` renders both as a standalone page and inside
[EpisodeReaderOverlay](../thestill/web/frontend/src/components/EpisodeReaderOverlay.tsx)
(its own scroll container and `z-50` focus trap — a route-based check would
mis-detect it, and a global floating tile above the overlay would fight the
focus trap while one below it would vanish behind the scrim). Surfaces
register/unregister a slot with the media layer on mount/unmount.

## 4. Playback-asset manifest (replaces a `mediaType` flag)

The episode API grows an explicit manifest instead of overloading
`audio_url`:

```ts
playback: {
  kind: 'audio' | 'video'
  audio?: { url, mimeType, duration, timelineOffset }
  video?: { url, mimeType, width, height, duration, timelineOffset }
  posterUrl?: string
  captionsUrl?: string
}
```

- Per-asset `timelineOffset` generalizes
  `playback_time_offset_seconds`
  ([podcast.py:159](../thestill/models/podcast.py#L159)) — renditions of the
  "same" content already drift (trimmed leading silence, pre-roll); a
  separately fetched or transcoded video rendition gets its own offset.
- Assets use immutable IDs / versioned keys.
- The manifest also records **provenance/entitlement** for the video asset
  (publisher enclosure vs. platform-restricted source, §6).

## 5. Real audio-first mode: two distinct toggles

A hidden playing `<video>` keeps downloading and decoding its video track —
that is a visual-off toggle, not audio-only. Both semantics exist and are
kept distinct:

- **Hide video** (`videoPreference: 'audio-only'`): instant, same resource,
  no continuity break, little network saving.
- **Use audio rendition**: a controlled source transition to the audio asset
  preserving logical position/rate/play state (offset-adjusted). Saves data
  and battery; the mode for commutes and backgrounding.

This requires new source-selection logic: the current same-episode branch in
[PlayerContext.tsx:69](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L69)
resumes without ever comparing the source URL, so today a rendition switch
would silently keep playing the old source.

Where possible, **generate audio and video renditions from the same retained
master** and validate duration/alignment at ingest — the cheapest way to
keep karaoke sync honest across renditions. (`requestVideoFrameCallback()`
is noted as a more accurate video presentation clock if frame-level accuracy
is ever needed; the current rAF + `currentTime` loop is smooth word
highlighting, not frame-accurate, and stays as-is.)

## 6. Sources, engines, and the YouTube question

### What the backend actually has today

- YouTube ingestion downloads **audio only** — `bestaudio` extracted to
  `.m4a` ([youtube_downloader.py:190](../thestill/core/youtube_downloader.py#L190));
  no video rendition is retained.
- Original media is deleted after downsampling when
  `delete_audio_after_processing` is set
  ([task_handlers.py:318](../thestill/core/task_handlers.py#L318)), and the
  episode model has only audio-oriented enclosure metadata and paths.
- There is no episode media route; the API exposes the publisher
  `audio_url`. Production storage can be S3 with presigned retrieval
  ([s3.py:314](../thestill/utils/file_storage/s3.py#L314)) — proxying video
  bytes through FastAPI is the wrong cloud path.

### Serving plan

- **Local/dev:** authenticated range-serving route. Requirements: `206` +
  `Content-Range` + `Accept-Ranges`, correct MIME types, `HEAD`, cache
  validators.
- **S3/prod:** short-lived presigned URLs (or CDN) in the manifest — the
  Phase-4 presigned work deferred from #35/#34.
- **Encode policy:** a "720p cap" is a container/codec target, not just a
  height cap — MP4 / H.264 / AAC with fast-start (`moov` up front) for broad
  Safari compatibility.
- Retaining video conflicts with the delete-after-downsample lifecycle;
  video retention is a per-episode storage decision with a disk/S3 cost cap
  (open item).

### Engine adapter boundary

- **Native engine** (the stable `<video>` node) for directly playable,
  licensed assets: RSS `video/mp4` enclosures, `<podcast:alternateEnclosure>`
  MP4 variants (#39's table), locally produced renditions (#34 briefing
  audio later).
- **YouTube iframe engine** where compliance requires it. YouTube's
  developer policies prohibit downloading/caching AV content, separating
  audio/video components, and background playback. The pipeline already
  downloads YouTube audio for transcription, so playing back what's on disk
  changes little for a **self-hosted personal instance** — but for any
  distributed/hosted deployment this is a product/legal decision, recorded
  as an explicit gate, not an engineering default. The iframe engine samples
  time (~250 ms polling) and interpolates between samples for smooth
  highlighting, correcting after seeks — degraded but workable karaoke.
  #39's YouTube-ID extraction and variant selection are reused here.

**The first legitimate video population is RSS video enclosures** — fully
licensed, directly playable, already flowing through the feed parser
(`audio_mime_type` captures enclosure type; #39's
`episode_alternate_enclosures` table holds variants). YouTube video waits on
the gate above and blocks nothing.

## 7. Karaoke sync constraints (from #38 / #23)

- The session must keep exposing a synchronous `getCurrentTime()` cheap
  enough for a 60 fps rAF loop — native engine: read the element; iframe
  engine: interpolated sample.
- Reported time is **logical episode time**: engine time adjusted by the
  active asset's `timelineOffset`, so `SegmentedTranscriptViewer` and word
  highlighting are rendition-agnostic.
- Rendition/engine switches must not glitch highlighting: apply offset
  before announcing the new position.

## 8. Scope additions to the player

Poster frame, captions track (`captionsUrl` → `<track>`), media error
surface, buffering states beyond the current `isLoading`, fullscreen,
volume/mute, keyboard and screen-reader accessibility for all new surfaces
(theater, floating tile, PiP button). The `PlayerContext` API grows; it does
not merely gain a `videoUrl`.

## 9. Alternatives considered and rejected

1. **Moving React portals / DOM reparenting for surface changes.** Portals
   remount on target change; DOM moves pause `<video>`; `moveBefore()` is
   not cross-browser. If testing ever shows reparenting is safe on all
   supported browsers, it may be encapsulated behind the media layer with a
   continuity regression test — but it is not assumed.
2. **YouTube iframe as the primary engine for all video.** Coarse polling
   breaks smooth karaoke, no unified transport, ads/consent inside the
   reader. Retained only as the compliance adapter (§6).
3. **Permanently separate `<audio>` + `<video>` pair.** Two sources of
   truth → both playing at once, a mini player that lies, duplicated seek
   and sync plumbing.
4. **Modal/lightbox video player.** Blocks the transcript — the app's
   differentiator — and orphans playback on close.
5. **Live video thumbnail in the mini-player bar.** Incompatible with one
   visual element; canvas snapshot cost for a 40×40 payoff (§2).
6. **Auto-PiP on navigation as policy.** Needs transient activation, can
   reject, support varies; Chrome's automatic-PiP paths are conditional on
   browser settings + Media Session, not a navigation guarantee.

## 10. Precedent: Spotify's audio/video model

Spotify's video-podcast architecture matches this design almost point for
point: one episode with audio and video renditions and one playback session;
a real video↔audio rendition switch that preserves position (and drops to
audio automatically on backgrounding/lock); artwork — not live video — in
the mini bar; user-initiated pop-out/PiP; timeline alignment solved at
ingestion by deriving the audio rendition from the uploaded video master.
The one structural difference: Spotify owns ingestion, so licensing is a
precondition of upload and no third-party engine is ever needed. Thestill
ingests other people's feeds — the engine adapter boundary (§6) is the
honest substitute for the piece Spotify got to delete by owning the supply
side. (Spotify's transcript sync is paragraph-level; thestill's word-level
karaoke is the stricter constraint, hence §7.)

## 11. Implementation increments

Ship in three increments; each is independently valuable and none blocks on
the YouTube legal decision.

### Increment 1 — Media Session + session groundwork

1. Media Session integration in `PlayerProvider` (feature-detected handlers,
   artwork, ±15 s, position state). Benefits audio today.
2. Introduce the session/presentation state split internally (no visible
   change): stable media node in a global layer, `MiniPlayer` reads through
   it. Swap `<audio>` → `<video>` element (audio-only sources; no visual
   surface yet).
3. Fix the same-episode resume branch to compare source URLs (prerequisite
   for rendition switching).

### Increment 2 — Native video for licensed assets

1. Playback-asset manifest in the episode API (audio-only episodes emit
   `kind: 'audio'` with the existing URL — no client break).
2. Theater surface in `EpisodeReader` (page + overlay) via slot
   registration; poster; karaoke sync through logical time.
3. Serving: local range route + S3 presigned path.
4. Population: RSS video-enclosure episodes and #39's MP4
   alternate-enclosure variants. (#39's YouTube-embed slice can ship
   independently before this; its embeds later migrate onto the iframe
   engine.)
5. Continuity regression test: surface changes never reset playback.

### Increment 3 — Floating/PiP + true rendition switching

1. Desktop floating tile; explicit PiP button; PiP event listeners.
2. `Hide video` vs `Use audio rendition` toggles; offset-adjusted source
   transitions; mobile audio-first backgrounding.
3. YouTube iframe engine behind the adapter boundary — **gated on the
   product/legal decision** for hosted deployments; provenance recorded in
   the manifest.

## 12. Risks

- **Continuity regressions** — the whole point is uninterrupted playback;
  the regression test in Increment 2 is non-optional.
- **Storage cost of retained video** vs. the delete-after-downsample
  lifecycle; needs an explicit cap and lifecycle policy.
- **Rendition timeline drift** breaking karaoke; mitigated by
  single-master derivation + ingest validation + per-asset offsets.
- **Overlay z-index/focus interactions** for floating surfaces; mitigated
  by surface registration (the overlay simply doesn't register a floating
  slot).
- **YouTube policy exposure** for any distributed build; gated, with
  provenance stored.

## 13. Open items

- Video retention policy: which episodes keep a video rendition, size cap,
  S3 lifecycle.
- The hosted-deployment YouTube decision (product/legal), and whether the
  iframe engine ships at all before it.
- Whether #39 ships first as the interim episode-page embed (recommended:
  yes — it's ~2–3 h and its data model feeds §4/§6) or is folded directly
  into Increment 2.
- `captionsUrl` production: derive WebVTT from the cleaned transcript
  sidecar (#18) — likely trivial and high-value, but unscoped here.
