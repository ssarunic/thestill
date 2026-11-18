# contexts/playback-engine

## Overview

The spec #62 engine adapter boundary under `PlayerContext`: a `PlaybackEngine` interface with two imperative implementations — `NativeEngine` over the one stable `<video>` element (HLS via hls.js where needed, spec #113) and `YouTubeEngine` over the YouTube IFrame Player API. Engines hold no React state; `PlayerContext` turns their `EngineEvents` callbacks into state. The native engine also carries the spec #116 timeline: a verified piecewise `TimelineMap` between transcript time and the listener's copy, and a silent hold of a transcript-time start until that map arrives.

## Interface

- `PlaybackEngine`, `EngineKind` (`'native' | 'youtube'`), `EngineEvents` (native-only `onTimelineHoldChange`), `EngineSource` (native-only `crossOrigin`, `elementUrl`), `LoadOptions` (native-only `holdForTimeline`) — the engine contract [src/contexts/playback-engine/types.ts:PlaybackEngine]
- `NativeEngine`, `NativeEngineState` (also carries `elementUrl` and `timeline`) — `<video>` element engine; `load`, `unload`, `play`, `pause`, `seekTo(seconds, { holdForTimeline })`, `setPendingSeek`, `setTimeline`, `hasTimeline`, `releaseTimelineHold`, `isHoldingForTimeline`, `heldTranscriptTime`, `getElementTime`, `getElementDuration`, `canPlayNow`, `hasLoadedAnything`, … [src/contexts/playback-engine/native-engine.ts:NativeEngine]
- `AppliedTimeline` (`map` + transcribed copy's `durationS`), `AUDIO_NOT_LOADED_MESSAGE`, `TIMELINE_HOLD_MAX_MS` (10 000) [src/contexts/playback-engine/native-engine.ts:AppliedTimeline]
- `TimelineMap` (`spans`, `gaps`, `constant`, `isIdentity`, `toAsset`, `toLogical`, `toLogicalHeld`, `listenerAdAt`, `classifyBreak`), `MapSpan`, `SourceGap` (`confirmed|unresolved`), `AssetTime`, `ListenerAd`, `BreakState` (`same|different|absent|unresolved`) — spec #116 piecewise transcript ↔ listener-copy map [src/contexts/playback-engine/timeline-map.ts:TimelineMap]
- `YouTubeEngine` — IFrame API engine with a polled, interpolated clock [src/contexts/playback-engine/youtube-engine.ts:YouTubeEngine]
- `loadYouTubeIframeApi`, `YTPlayer`, `YTNamespace` — lazy, idempotent IFrame API loader [src/contexts/playback-engine/youtube-iframe-api.ts:loadYouTubeIframeApi]
- `isHlsSource`, `canPlayHlsNatively`, `needsHlsJs`, `loadHls`, `preloadHls`, `HLS_ERROR_EVENT`, `HlsModule` — HLS routing and lazy hls.js loading [src/contexts/playback-engine/hls.ts:needsHlsJs]
- `FakeYTPlayer`, `fakePlayers`, `resetFakePlayers`, `fakeYouTubeApi` — shared test fixture for the IFrame API (not collected as a test) [src/contexts/playback-engine/youtube-player-fake.ts:fakeYouTubeApi]

## Invariants

- Engines hold no React state; exactly one engine is active at a time and both engines' DOM nodes coexist with at most one visible [src/contexts/playback-engine/types.ts:PlaybackEngine]
- The `<video>` element is the one stable media node: `unload` detaches the source but keeps the element [src/contexts/playback-engine/native-engine.ts:NativeEngine]
- hls.js is used wherever Media Source Extensions exist except on Apple WebKit; native HLS only there or where MSE is missing (Chromium's built-in HLS fails seeks) [src/contexts/playback-engine/hls.ts:needsHlsJs]
- When switching to hls.js the outgoing source is cleared first so the old file never keeps playing while the chunk loads [src/contexts/playback-engine/native-engine.ts:load]
- A carried rate sets both `defaultPlaybackRate` and `playbackRate` so it survives the source change [src/contexts/playback-engine/native-engine.ts:load]
- An explicit seek supersedes a deferred (`loadedmetadata`) seek; a resume position learned later never applies after a user seek, and after metadata only within `LATE_SEEK_WINDOW_SECONDS` (5) of the start [src/contexts/playback-engine/native-engine.ts:setPendingSeek]
- `pause()` cancels a `play()` still waiting on an hls.js attach [src/contexts/playback-engine/native-engine.ts:pause]
- A map belongs to one verified load of one copy: every `load` and `unload` drops it, and `setTimeline` refuses a URL that is not the loaded source or an hls.js source [src/contexts/playback-engine/native-engine.ts:setTimeline]
- While a map is set, the engine speaks transcript time: `seekTo` goes through `toAsset`, `getCurrentTime` through `toLogicalHeld` (the clock stands still through an ad only the listener's copy has), `getDuration` is the transcribed copy's `durationS`; `getElementTime` / `getElementDuration` stay on the element's own clock, and deferred (`loadedmetadata`) seeks are always element time [src/contexts/playback-engine/native-engine.ts:getElementTime]
- A held transcript-time start waits PAUSED, never muted (Safari ends a muted audio-only file); the app still sees it as playing; it lands on `toAsset(asked)` when the map arrives, else on the asked time uncorrected after `TIMELINE_HOLD_MAX_MS` or `releaseTimelineHold`; any other seek ends the hold; a paused hold is re-held on the next `play()`; HLS sources never hold [src/contexts/playback-engine/native-engine.ts:beginHold]
- A source loaded with `crossorigin` that errors is reloaded once without it (`rejectCrossOrigin` stores a `no_cors` verdict), keeping the position and dropping the map [src/contexts/playback-engine/native-engine.ts:reloadWithoutCrossOrigin]
- `elementUrl` is what the element loads; `url` stays the source's identity everywhere else [src/contexts/playback-engine/types.ts:EngineSource]
- Safari's false end (ended within 0.5 s of the last seek and ≥ 2 s short of the longest reported duration) is recovered once per place by reloading the same source at that position [src/contexts/playback-engine/native-engine.ts:recoverFromFalseEnd]
- An error before any of the file arrived is reported as `AUDIO_NOT_LOADED_MESSAGE`, and not at all while nobody has asked to play (a restored session preloading); `play()` on an errored source loads it afresh [src/contexts/playback-engine/native-engine.ts:play]
- `TimelineMap.toAsset` sends a time inside a confirmed gap to the next span's start and an unresolved time with a neighbour's offset, flagged `approximate`; `toLogical` is null inside the listener's own audio; own audio under 1 s is a seam, not an ad [src/contexts/playback-engine/timeline-map.ts:toAsset]
- The YouTube engine never plays without its iframe visibly presented (policy enforced in `PlayerContext`) and never downloads or caches media [src/contexts/playback-engine/youtube-engine.ts:YouTubeEngine]
- YouTube clock: polled every 250 ms (`POLL_INTERVAL_MS`), interpolated while playing, frozen during ads/buffering, re-based on seek; an empty accepted-rate list means "not known yet", never "nothing allowed" [src/contexts/playback-engine/youtube-engine.ts:POLL_INTERVAL_MS]
- A failed hls.js chunk or IFrame API script load is forgotten so the next attempt retries [src/contexts/playback-engine/youtube-iframe-api.ts:loadYouTubeIframeApi]

## Side Effects

- Injects `<script src="https://www.youtube.com/iframe_api">` on first YouTube playback and chains `window.onYouTubeIframeAPIReady` [src/contexts/playback-engine/youtube-iframe-api.ts:loadYouTubeIframeApi]
- Sets `src`, `crossorigin`, `currentTime`, `playbackRate` and media event listeners on the `<video>` element; attaches hls.js instances [src/contexts/playback-engine/native-engine.ts:NativeEngine]
- A `TIMELINE_HOLD_MAX_MS` timer per held start; stores the `no_cors` verdict through `rejectCrossOrigin` (`src/drift/corsVerdict.ts`) [src/contexts/playback-engine/native-engine.ts:beginHold]
- `setInterval` clock poll while a YouTube player is alive [src/contexts/playback-engine/youtube-engine.ts:YouTubeEngine]
- Lazy `import('hls.js/light')` plus its worker URL, memoised [src/contexts/playback-engine/hls.ts:loadHls]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
