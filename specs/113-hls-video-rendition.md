# HLS Video Rendition for `<podcast:alternateEnclosure>` Streams

> **Status:** 🚧 Active development
> **Created:** 2026-11-04
> **Updated:** 2026-11-04
> **Priority:** Medium. Publishers on Flightcast already ship video for
> every episode, and Apple Podcasts plays it. Thestill stores the stream
> and plays only the MP3.
> **Author:** Product & Engineering
> **Related:** [#61 unified-av-playback-session](61-unified-av-playback-session.md)
> (session, renditions, `timeline_offset`, theater/floating/PiP),
> [#62 youtube-video-rendition](62-youtube-video-rendition.md)
> (alternate-enclosure observer, engine boundary),
> [#39 video-alternate-enclosure-player](39-video-alternate-enclosure-player.md)
> (archived; deferred hls.js "until a publisher actually serves
> `application/x-mpegurl`"), [#25 security-audit-and-hardening](25-security-audit-and-hardening.md)
> (CSP), [#95 product-analytics](95-product-analytics.md) (same-origin
> `/ingest`)

---

## Overview

Flightcast feeds carry an MP3 `<enclosure>` and, next to it, an HLS
video stream:

```xml
<enclosure url="https://episode.flightcast.com/01M6FYA4GH9W4DYQQG4WV857SE.mp3" type="audio/mpeg"/>
<podcast:alternateEnclosure type="application/x-mpegURL" title="HLS Video Stream" rel="alternate" default="false">
  <podcast:source uri="https://episode.flightcast.com/hls/v/01M6FYA4GH9W4DYQQG4WV857SE.m3u8"/>
</podcast:alternateEnclosure>
```

Spec #62's observer already stores these rows (the local DB holds 10,
all from *SOLVED with Mark Manson*). *A Bit of Optimism* has 63 more in
its feed. The playback manifest ignores them: it only knows `video/*`
enclosures and `video/youtube` alternates.

This spec turns an HLS (or progressive `video/*`) alternate enclosure
into the manifest's native `video` asset. It keeps the MP3 as the `audio`
asset, and the episode becomes `kind: 'video'`. Spec #61's session does
the rest: theater on the reader, floating tile, PiP, "Use audio" switch.
Safari and iOS play HLS natively in the stable `<video>` element. Chrome
and Firefox get hls.js, loaded on demand into the same element.
Transcription stays on the RSS audio, unchanged.

## Goals

1. An episode with an HLS alternate enclosure plays its video in the
   reader's theater by default, in every current browser.
2. "Use audio" switches to the MP3 at the same logical position, and
   back. The existing #61 rendition switch already does this.
3. hls.js costs nothing on pages that never play HLS: it's a lazy chunk,
   and Safari never loads it.
4. The "Timestamps may have drifted" banner compares the transcript
   against the rendition actually playing.
5. hls.js works under the CSP, with the smallest relaxation that holds up
   when publisher CDNs change.

## Non-goals

- **Drift correction.** Some shows insert ads only into the MP3 (see
  Background). That's handled the same way as any other drift today: the
  banner warns, and nothing is corrected. Alignment for both cases is
  planned separately.
- **Quality menu.** hls.js adapts on its own, capped to the player's size.
- **HLS subtitles.** Flightcast ships a WebVTT subtitle group. Captions
  stay #61's open `captions_url` item.
- **Media Session chapter or artwork changes.**
- **Proxying HLS through our server.** Video bandwidth (0.5–5 Mbps per
  viewer) doesn't belong on the EC2 box.

## Background findings

- The manifest builder is
  [playback.py:93](../thestill/services/playback.py#L93). `kind: 'video'`
  happens only when `episode.audio_mime_type` is `video/*`
  ([playback.py:123](../thestill/services/playback.py#L123)), and that
  branch emits `audio: None`. Alternates are scanned only for
  `video/youtube`
  ([playback.py:59](../thestill/services/playback.py#L59)).
- Every manifest caller already passes all of an episode's alternate
  enclosures: the episode detail and list routes and the track payload in
  `listening_progress_service`. No repository change is needed.
- The observer stores every MIME type for the whole feed window on every
  refresh ([feed_manager.py:455](../thestill/core/feed_manager.py#L455),
  [media_source.py:1456](../thestill/core/media_source.py#L1456)). Rows
  already exist, so no backfill is needed.
- The frontend already handles a manifest carrying both renditions:
  `trackMediaKind`
  ([PlayerContext.tsx:179](../thestill/web/frontend/src/contexts/PlayerContext.tsx#L179)),
  `selectSource` (`:185`), `switchRendition` (`:630`), the presentation
  machine (`:851`), and `canSwitchRendition` behind TheaterSurface's
  "Use audio".
- `NativeEngine.load` assigns `el.src` directly
  ([native-engine.ts:103](../thestill/web/frontend/src/contexts/playback-engine/native-engine.ts#L103)).
  This is the seam for HLS attachment. `unload` (`:120`) and `destroy`
  (`:201`; it carries state across StrictMode remounts) must also tear
  down or hand over an hls.js instance.
- The drift banner probes `episode.audio_url` with a throwaway
  `new Audio()`
  ([EpisodeReader.tsx:864](../thestill/web/frontend/src/components/EpisodeReader.tsx#L864),
  used at `:944`, threshold 15 s at `:894`). With video first, that's
  the wrong rendition.
- The CSP is in
  [security_headers.py:51](../thestill/web/middleware/security_headers.py#L51).
  `connect-src 'self'` blocks hls.js's XHR, and `media-src 'self' https:`
  blocks the MSE `blob:` URL. `worker-src` is absent, so it falls back to
  `script-src`, which blocks hls.js's default inline-blob worker.
- **Live checks (2026-11-04):**
  - *Optimism* episode `01M6FYA4…` has an HLS master with 5 AVC variants
    (240p–1080p, 0.58–5.5 Mbps) and a subtitle group. Segments are
    MPEG-TS (`video/mp2t`), so hls.js must transmux.
  - CORS is `*` on the master and variant playlists. Segments 302 from
    `cascade.flightcast.com` to signed OVH S3 URLs
    (`s3.us-east-va.io.cloud.ovh.us`), which echo the origin.
  - Cross-correlating MP3 and HLS audio for *Optimism* gives an offset of
    −0.03 s from 5 s to 3440 s (corr 0.99). The renditions share a master;
    the only differences are a quiet first second and a ~6 s longer
    outro.
  - All 10 *SOLVED* episodes (Megaphone MP3, Flightcast video) have a
    feed duration 270–703 s longer than the HLS playlist. Ads are in the
    MP3 and not the video, so a static offset cannot align them.

## Clarifications

1. **Drifting video (SOLVED)?** User: *"for drifted audio we currently
   only show warning that transcript drifted. We should probably keep the
   same logic for now (i.e. don't do anything special). We anyway plan to
   address drifting and alignment in the future and we can do that for
   both then."* So there's no duration gate or per-host heuristic. Every
   HLS alternate is offered. The banner has to compare against the
   rendition that's playing (Goal 4).
2. **Default rendition?** User: **Video first (like Apple).** The
   manifest is `kind: 'video'` with both assets. This is #61's existing
   path: start on video, "Use audio" to switch.
3. **CSP?** The options and their security implications were laid out:
   `connect-src` only matters after XSS, and `img-src https:` already
   allows one-way exfiltration. An exact host list is weaker than it
   looks because OVH S3 is a public store, and it breaks when hosts
   change. Safari-only would mean no change. User: **`connect-src 'self'
   https:`** plus `media-src blob:`. `worker-src` stays at the
   `script-src` fallback, because the hls.js worker is served
   same-origin.

## Design

### Manifest (`services/playback.py`)

`_select_video_asset(episode, alternates)` picks the native video
alternate:

- **Candidates:** `mime_type` is `application/x-mpegurl` or
  `application/vnd.apple.mpegurl` (case-insensitive), or `video/*`
  except `video/youtube`.
- **Source URI:** must parse as `https://` with a host. Feed data is
  untrusted. `http:` would be mixed content, and `ipfs:` won't play.
  Anything else is skipped with a warning, the same hygiene the YouTube
  id check applies.
- **Order:** `is_default` first, then HLS before progressive (adaptive
  bitrate), then observation order.
- **Asset:** `{url, mime_type, duration: episode.duration,
  timeline_offset: episode.playback_time_offset_seconds, height}`. The
  audio and video share the episode's offset. Measured drift from
  inserted ads is out of scope (Clarification 1).

`build_playback_manifest`:

| Episode | kind | audio | video |
|---|---|---|---|
| `video/*` enclosure (unchanged) | video | null | enclosure |
| audio enclosure + native video alternate (**new**) | video | MP3 | alternate |
| audio enclosure, no alternate (unchanged) | audio | MP3 | null |

`youtube` and `poster_url` work as today. A video-kind manifest always
carries a poster.

### Engine (`playback-engine/`)

- **`hls.ts` (new):**
  - `isHlsSource(url, mime)`: by MIME type, falling back to an `.m3u8`
    path when the asset carries none. `canPlayHlsNatively(el)`.
  - `needsHlsJs(el, url, mime)`: an HLS source uses hls.js wherever
    Media Source Extensions (`MediaSource` / `ManagedMediaSource`) exist,
    except on Apple WebKit (`WebKitPlaybackTargetAvailabilityEvent` in
    `window`: Safari and every iOS browser). There, native HLS keeps
    AirPlay, lock-screen and background audio. Native HLS is also used
    where MSE is missing but `canPlayType` says yes.
    - *Revised during the Phase C browser check, 2026-11-04.* Chrome 156
      reports `canPlayType('application/vnd.apple.mpegurl') === 'maybe'`.
      Its built-in HLS then fails **every** seek on Flightcast VOD
      (`PipelineStatus::DEMUXER_ERROR_COULD_NOT_PARSE`, before or during
      playback). hls.js seeks the same stream fine in the same browser.
      So `canPlayType` alone can't decide.
    - If `Hls.isSupported()` turns out false after loading, the engine
      falls back to native `src` when `canPlayType` allows it, else
      reports the error.
  - `loadHls()`: a memoised dynamic `import('hls.js/light')` plus the
    worker URL (`hls.js/dist/hls.worker.js?url`, a same-origin Vite
    asset).
  - `preloadHls()`.

  The light build has no subtitles, alternate audio, EME or CMCD, none
  of which are needed.
- **`EngineSource`** gains `mimeType?: string | null`. `ActiveSource` in
  PlayerContext carries the asset's `mime_type` through `beginSource` →
  `load`.
- **`NativeEngine.load`:** any prior hls.js instance is destroyed first.
  Then:
  - **Native path** (Apple WebKit, no MSE, or non-HLS): `el.src = url`
    as today.
  - **hls.js path:** `el.removeAttribute('src')`. When the module loads,
    construct `Hls({ workerPath, capLevelToPlayerSize: true })`, call
    `attachMedia(el)` and `loadSource(url)`. A load superseded while the
    import is pending is dropped (generation counter).

  The `loadedmetadata` deferred seek, rate carry, error suppression and
  `setPendingSeek` keep working unchanged, because MSE drives the same
  element events.
- **`play()` during attachment** waits for the attach promise, then calls
  `el.play()`. Chrome and Firefox honour sticky activation, so an awaited
  play still counts as user-initiated. Safari never takes this path.
- **Fatal hls.js errors:** one `recoverMediaError()` for `MEDIA_ERROR`.
  Anything else, or a second failure, calls `onError(...)` with the same
  messages `describeMediaError` uses, so TheaterSurface's existing error
  surface and "Use audio" apply. If `Hls.isSupported()` is false, it
  reports "This media format is not supported by your browser."
- **`unload()`** destroys the instance before clearing `src`.
  **`destroy()`** hands the instance to its successor through
  `NativeEngineState`, the same way StrictMode remounts already hand over
  `url` and `pendingSeek`.
- **`capLevelToPlayerSize`** also cuts bandwidth while the video is
  hidden behind the audio-first mini player: the zero-size layer picks
  the lowest level. "Use audio" is still the real saver (#61 §5).

### Reader

- **Drift banner:** while this episode is the current track on the native
  engine and that engine has reported a duration, compare the transcript
  against `player.nativeDuration`, the rendition actually loaded.
  Otherwise fall back to today's `audio_url` probe.
  - `nativeDuration` is a new PlayerContext field. It's null until the
    native engine reports one, and it resets on every source change.
    `player.duration` can't be used: it starts as the feed's
    `durationHint`.
  - The raw duration is compared with no offset applied. The transcript's
    `transcript_source_duration_s` is a raw file duration too.
- **Preload:** the reader calls `preloadHls()` when the episode's
  `playback.video` is HLS and the browser needs hls.js. The first click
  then finds the module cached.

### CSP (`web/middleware/security_headers.py`)

```text
connect-src 'self' https:;
media-src 'self' https: blob:;
```

The spec #95 test keeps its intent: no analytics host is listed, and
`posthog` appears nowhere. It no longer asserts `connect-src == 'self'`.

## Execution plan

### Phase A — Manifest + CSP

| # | File | Change |
|---|---|---|
| A1 | [services/playback.py](../thestill/services/playback.py) | `_select_video_asset`, HLS/progressive alternates → `kind: 'video'` with both assets; docstrings |
| A2 | [tests/unit/services/test_playback_manifest.py](../tests/unit/services/test_playback_manifest.py) | HLS alternate → video kind + audio kept; vnd.apple alias; `video/mp4` alternate; default wins; HLS beats progressive; non-https skipped; video enclosure unchanged; youtube alongside |
| A3 | [web/middleware/security_headers.py](../thestill/web/middleware/security_headers.py) | `connect-src 'self' https:`, `media-src … blob:`, comment |
| A4 | [tests/unit/security/test_security_headers.py](../tests/unit/security/test_security_headers.py) | assert the new directives; analytics test keeps "no posthog host" |

**Gate:** `./venv/bin/pytest tests/unit/services/test_playback_manifest.py tests/unit/security/test_security_headers.py` green.

### Phase B — hls.js in the native engine

| # | File | Change |
|---|---|---|
| B1 | `web/frontend/package.json` + lock | `hls.js` `^1.7.3`, released 2026-09-11; 1.7.4 is only 7 days old. It has no dependencies, so the lock entry is added surgically (macOS `npm install` prunes Linux optionals). Validate with `npm ci` |
| B2 | `src/contexts/playback-engine/hls.ts` (new) | `isHlsSource`, `canPlayHlsNatively`, `needsHlsJs`, `loadHls`, `preloadHls`, `HLS_ERROR_EVENT` |
| B3 | `src/contexts/playback-engine/types.ts` | `EngineSource.mimeType` |
| B4 | `src/contexts/playback-engine/native-engine.ts` | hls.js attach/detach/handoff, pending-attach play, fatal-error mapping |
| B5 | `src/contexts/PlayerContext.tsx` | `ActiveSource.mimeType` through `selectSource` → `beginSource` → `load` |
| B6 | `src/contexts/playback-engine/native-engine.hls.test.ts` (new) | fake Hls module: attach on HLS+no native, native path when `canPlayType` says yes, superseded load, unload destroys, fatal → onError, recover once |

**Gate:** `npx vitest run src/contexts`, `npx tsc --noEmit`, `npx eslint` on touched files, and `npx vite build` showing hls.js in its own chunk, separate from the entry chunk.

### Phase C — Reader: drift banner + preload

| # | File | Change |
|---|---|---|
| C1 | `src/components/EpisodeReader.tsx` | live-rendition duration for the drift banner; `preloadHls()` for HLS video episodes |
| C2 | EpisodeReader tests | banner uses the loaded rendition's duration when current; probe otherwise |
| C3 | `src/contexts/PlayerContext.tsx` | `nativeDuration` (engine-reported, reset per source) |

**Gate:** frontend vitest suite green. **Browser check** on a scratch
server against a copy of the local DB, using a SOLVED episode (HLS row
present). Chromium via Playwright, private port:

- the theater plays the HLS video (network shows `.m3u8` + `.ts`);
- "Use audio" switches to the MP3 at the same position, and back;
- the drift banner shows, because the video is minutes shorter;
- no CSP violations in the console.

## Alternatives considered

- **Duration gate.** Probe the HLS playlist after refresh, store its
  duration, and offer video only within ~10 s of the audio. Rejected by
  the user (Clarification 1): drift has one policy today (warn), and
  alignment will be solved once for both renditions.
- **Audio first, opt-in video.** Better for background listening and
  data, and leaves publisher MP3 counts untouched. Rejected for an
  Apple-like default (Clarification 2). "Use audio" remains one tap
  away.
- **Exact CSP host allow-list.** Weaker than it looks, because
  `s3.us-east-va.io.cloud.ovh.us` is a public object store, and brittle:
  segments already sit behind signed redirects to a third-party store.
- **Safari-only native HLS.** No CSP change, but no video for desktop
  Chrome and Firefox.
- **Server-side HLS proxy.** It keeps `connect-src 'self'`, but puts
  video bandwidth on EC2.
- **A separate `HlsEngine`.** hls.js drives the same `<video>` element,
  so a second engine would duplicate every transport method and the #61
  invariant-2 node handling for no gain. It lives inside `NativeEngine`.
- **Full hls.js build.** Subtitles, alternate audio and DRM aren't used,
  so the light build is enough.

## Files touched

**Modified:** `thestill/services/playback.py`,
`thestill/web/middleware/security_headers.py`,
`thestill/web/frontend/package.json`,
`thestill/web/frontend/package-lock.json`,
`thestill/web/frontend/src/contexts/playback-engine/types.ts`,
`thestill/web/frontend/src/contexts/playback-engine/native-engine.ts`,
`thestill/web/frontend/src/contexts/PlayerContext.tsx`,
`thestill/web/frontend/src/components/EpisodeReader.tsx`, tests,
`specs/README.md`

**Created:** `specs/113-hls-video-rendition.md`,
`thestill/web/frontend/src/contexts/playback-engine/hls.ts`

## Open questions

- ~~Does Chrome's native HLS report `canPlayType` 'maybe' on desktop?~~
  **Settled 2026-11-04:** yes (Chrome 156), and it fails seeks on
  Flightcast streams. Chromium uses hls.js (see the `needsHlsJs` rule).
  Worth re-testing when Chrome's built-in HLS matures. The rule could
  then narrow back to `canPlayType`.
- **Publisher download counts.** Video-first playback fetches no MP3, so
  the publisher's MP3 analytics don't see these plays. That's accepted
  with Clarification 2 and is the same as Apple's behaviour.

## Rollback plan

Revert the PR. There's no schema change and no data rewrite: rows in
`episode_alternate_enclosures` were already being written. Reverting
`playback.py` alone restores audio-only manifests, and the CSP change
reverts with it.
