# drift

## Overview

The client half of spec #116 drift alignment. When the listener plays an episode that has a sync track, the reader fetches the track's landmarks, streams this browser's copy of the MP3 through an aligner in a Web Worker, and classifies the transcript time between the offset spans it finds. It then checks, through the service worker in `public/sw.js`, that the audio element plays the copy that was aligned. Only then does it hand the player a `TimelineMap`. A session needs the `drift.align` permission and a browser on a user-agent allow-list. Every outcome is reported to the server. Admins see it in the `?driftdebug=1` strip.

## Interface

- `useDriftAlignment`, `DriftAlignmentState` (`idle | running | done`, `applied`, `recheckable`), `checkPlay`, `transcriptIntervals`, `COPY_URL_WAIT_MS` (2000), `resetDriftAttempts` — the reader hook: run, check, apply, report [src/drift/useDriftAlignment.ts:useDriftAlignment]
- `useDriftSession` — app-wide permission switch, mounted in `App.tsx` [src/drift/useDriftSession.ts:useDriftSession]
- `runDriftAlignment`, `DriftInput`, `DriftReport`, `DriftProgress`, `DriftOutcome`, `CopySource`, `MIN_COVERAGE` (0.9) — one alignment attempt [src/drift/run.ts:runDriftAlignment]
- `Aligner`, `align`, `Span`, `Alignment`, `MIN_RUN` (5), `MAX_MISSED_IN_RUN` (2), `OFFSET_TOLERANCE_S` (0.02) — streaming landmark matcher, a port of `align` in `thestill/core/sync_track.py` [src/drift/align.ts:Aligner]
- `classifyAlignment`, `TranscriptInterval`, `Classification` — splits time outside spans into `confirmed` / `unresolved` gaps [src/drift/classify.ts:classifyAlignment]
- `parseSyncTrack`, `SyncTrack`, `Anchor`, `BaseSpan`, `SyncTrackError`, `ALGORITHM_VERSION` (1), `CODEC_MP3` (1) — `TSYN` binary reader [src/drift/syncTrack.ts:parseSyncTrack]
- `Mp3FrameParser`, `frameHeader`, `FrameHeader`, `FrameSink` — header-only MPEG Layer III frame splitter, ID3v2-aware [src/drift/mp3Frames.ts:Mp3FrameParser]
- `crc32`, `adler32`, `windowDigest`, `WINDOW_FRAMES` (4), `BlockDigester`, `BLOCK_BYTES` (65536) [src/drift/digest.ts:BlockDigester]
- `verifyAgainst`, `MediaReport`, `FetchedCopy`, `Verification` (`verified | mismatch | unverified`) [src/drift/verify.ts:verifyAgainst]
- `toOutcomeBody`, `browserLabel`, `DriftOutcomeBody`, `PlayCheck`, `MAX_REPORTED_SPANS` (64), `MAX_REPORT_BYTES` (4096) [src/drift/report.ts:toOutcomeBody]
- `ensureDriftServiceWorker`, `removeDriftServiceWorker`, `listenForMediaReports`, `mediaReportsFor`, `elementCopyUrl`, `whenElementCopyUrl`, `onMediaReport`, `reloadOnceUncontrolled`, `serviceWorkerSupported`, `serviceWorkerControlling`, `SwState`, `SW_URL` (`/sw.js`); test seams `page`, `recordMediaReport`, `resetDriftServiceWorkerClient` [src/drift/swClient.ts:ensureDriftServiceWorker]
- For the player: `beginAudioLoad`, `rejectCrossOrigin`, `currentAudioLoad`, `getCorsVerdict`, `setCorsVerdict`, `chainKey`, `setCrossOriginLoadingEnabled`, `crossOriginLoadingEnabled` [src/drift/corsVerdict.ts:beginAudioLoad]; `setTimelineExpected`, `isTimelineExpected` [src/drift/expectation.ts:isTimelineExpected]
- `driftBrowserVerified(userAgent?, touchPoints?)`, `driftAlignmentSupported()`, `setUnverifiedBrowsersAllowed(on)` [src/drift/support.ts:driftAlignmentSupported]
- `useDriftAllowed()` [src/drift/useDriftSession.ts:useDriftAllowed]
- `DriftDebugStrip` (default export) — the admin panel, with seek buttons through `onPlayAt` [src/drift/DriftDebugStrip.tsx:DriftDebugStrip]

## Invariants

- Allowed browsers: none whose user agent matches `Mobile|Android|iPhone|iPad|iPod|CriOS|FxiOS|EdgiOS`. Then any user agent with `Chrome/` or `Chromium/`, or Mac Safari with 0 touch points. A build with `VITE_DRIFT_ANY_BROWSER=1` allows every browser [src/drift/support.ts:driftBrowserVerified]
- A session that holds `drift.align.unverified` is allowed in every browser. The answer is kept in `localStorage` (`thestill:drift:unverified`) for the next page load, and `driftAlignmentSupported()` is the list or that remembered answer [src/drift/support.ts:setUnverifiedBrowsersAllowed]
- Permission held means `drift.align` plus a verified browser or `drift.align.unverified` [src/drift/useDriftSession.ts:useDriftAllowed]. Nothing changes while auth is loading. If it is not held, crossorigin is turned off, the worker gets `drift-disable` and is unregistered, and a page it controlled reloads at most once per tab [src/drift/useDriftSession.ts:useDriftSession]
- The element gets `crossorigin` only when loading is enabled, the player calls the source eligible, and the redirect chain's stored verdict is `cors_ok`. The alignment's own audio fetch is the CORS probe. A `no_cors` outcome stores `no_cors`; any other outcome with `fetchedBytes` set stores `cors_ok` [src/drift/useDriftAlignment.ts:runAttempt]
- One attempt per `${episodeId}|${generation}|${audioUrl}` for the life of the page. It starts on the first `isPlaying`, and only for the native engine playing the `audio` rendition of a non-HLS asset. It is skipped when there is no `Worker` or when `navigator.connection.saveData` is set. A later load of the same audio is checked again against the stored report and is never downloaded again [src/drift/useDriftAlignment.ts:useDriftAlignment]
- The aligner prefers the element's own copy. If the element loaded with crossorigin under a controlling worker, the hook waits up to `COPY_URL_WAIT_MS` for the https `finalUrl` and fetches that URL. Otherwise it fetches the enclosure [src/drift/useDriftAlignment.ts:startAttempt]
- The track is fetched with `credentials: 'include'`, the audio with `mode: 'cors', credentials: 'omit'`. A thrown audio fetch is `no_cors`: a CORS refusal and a network failure look the same. Every other failure is an outcome, and only an abort throws [src/drift/run.ts:runDriftAlignment]
- Outcomes: coverage below `MIN_COVERAGE` is `low_confidence`. Every span offset within `OFFSET_TOLERANCE_S`, with the fetched length equal to the track's `audioBytes`, is `identity`; anything else is `aligned`. A non-MP3 track, or a file with no frames, is `unsupported_codec`. `variant_mismatch` comes only from `toOutcomeBody` when the check says `mismatch`. No client code produces `identity_assumed` [src/drift/run.ts:DriftOutcome]
- The aligner trusts an offset only after `MIN_RUN` landmarks agree within `OFFSET_TOLERANCE_S`, missing at most `MAX_MISSED_IN_RUN` in a row. It keeps the heaviest chain that moves forward in both copies. It must give the same answers as Python on `tests/fixtures/sync_track/align_vectors.json`: change both together [src/drift/align.ts:Aligner]
- Outside the spans, stretches under `ad_break` segments or under no segment are `confirmed`; other segments are `unresolved`. Pieces under 0.25 s fold into a neighbour. With no segments at all, everything is `unresolved` [src/drift/classify.ts:classifyAlignment]
- `verified` needs `lengthOk === true` and at least one checked block, with every checked block matching. A length mismatch or any unmatched block is `mismatch`. Safari's opening `bytes=0-1` request is ignored [src/drift/verify.ts:verifyAgainst]
- A map is applied only when the check is `verified`, the outcome is `aligned`, and there is at least one span. Any other verdict removes it. Later responses of the same load are checked again (debounced 500 ms). A changed verdict is reported again, up to `MAX_REVISIONS` (2) times. A `mismatch` is final. If the player refused a verified map and takes it when it is offered again, that report goes out as revision 3 [src/drift/useDriftAlignment.ts:keepChecking]
- Every map goes to the player with the transcript's `skippableAdBreaks` as `adBreaks` (spec #116 §6.2) [src/drift/useDriftAlignment.ts:useDriftAlignment]
- The check runs at 250 ms if it can already decide, otherwise at 1500 ms. If an `aligned` play cannot be checked and the player is holding the start, the hook reloads it once (`reloadHeldForCheck`). If nothing is held, the attempt is marked `recheckable` and the next held start reloads. Outcomes other than `aligned` release the hold at once. The engine caps a hold at `TIMELINE_HOLD_MAX_MS` (10 s) [src/drift/useDriftAlignment.ts:checkThisLoad]
- The episode is marked "map expected" while alignment is enabled, a generation exists, `Worker` exists, the asset is not HLS, `saveData` is off, and no check has ended without a map. The mark is cleared on unmount [src/drift/useDriftAlignment.ts:useDriftAlignment]
- Reports hold nothing that identifies the listener. `audio_host` must match `^[A-Za-z0-9.-]{1,253}$`, and `finalUrl` and block digests stay in the page. Spans are dropped from the end until the body fits in `MAX_REPORT_BYTES` [src/drift/report.ts:toOutcomeBody]

## Side Effects

- `GET /api/podcasts/{podcastSlug}/episodes/{episodeSlug}/sync-track/{generation}`, then a full `GET` of the audio file (a second download), both from the worker [src/drift/run.ts:runDriftAlignment]
- `POST /api/podcasts/{podcastSlug}/episodes/{episodeSlug}/drift-outcome` through `reportDriftOutcome`; errors are ignored [src/drift/useDriftAlignment.ts:useDriftAlignment]
- Starts the module worker `src/workers/driftAlign.worker.ts` and terminates it after `done` or `error` [src/drift/useDriftAlignment.ts:runAttempt]
- Registers `/sw.js` with scope `/` on the first play of an episode that has a track [src/drift/swClient.ts:ensureDriftServiceWorker]
- A `navigator.serviceWorker` `message` listener keeps the last 400 `{ kind: 'drift-media', report }` reports. It is added when the module loads if loading was enabled on the last page load [src/drift/swClient.ts:listenForMediaReports]
- Posts `{ kind: 'drift-disable' }` to the controlling worker and unregisters every registration whose script ends in `/sw.js` [src/drift/swClient.ts:removeDriftServiceWorker]; when the page was controlled, `reloadOnceUncontrolled` calls `window.location.reload()` at most once per tab [src/drift/swClient.ts:reloadOnceUncontrolled]
- `localStorage`: `thestill:drift:cors:<chainKey>` holds `{ v, t }` for 7 days, and a `t` more than 60 s in the future is ignored. `thestill:drift:enabled` holds `'1'` and is read when the module loads [src/drift/corsVerdict.ts:getCorsVerdict]
- `sessionStorage` key `thestill:drift:reloaded` [src/drift/swClient.ts:reloadOnceUncontrolled]
- Timers: the 250 / 1500 ms check and the 500 ms re-check debounce [src/drift/useDriftAlignment.ts:checkThisLoad], and the `COPY_URL_WAIT_MS` wait [src/drift/swClient.ts:whenElementCopyUrl]
- Player calls: `applyTimeline`, `releaseTimelineHold`, `reloadHeldForCheck` [src/drift/useDriftAlignment.ts:useDriftAlignment]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
