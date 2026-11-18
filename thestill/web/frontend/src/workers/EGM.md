# workers

## Overview

Dedicated Web Worker entry points. There is one: `driftAlign.worker.ts`, the spec #116 thread that runs a drift alignment off the main thread. Downloading the whole episode, hashing it and fitting spans then never block playback or scrolling. The file only passes messages; the logic is in [`src/drift/run.ts`](../drift/run.ts) (see [drift/EGM.md](../drift/EGM.md)).

## Interface

- `DriftWorkerRequest` — `{ type: 'start'; input: DriftInput }` [src/workers/driftAlign.worker.ts:DriftWorkerRequest]
- `DriftWorkerMessage` — `{ type: 'progress'; progress: DriftProgress } | { type: 'done'; report: DriftReport }` [src/workers/driftAlign.worker.ts:DriftWorkerMessage]
- The module entry is started by `new Worker(new URL('../workers/driftAlign.worker.ts', import.meta.url), { type: 'module' })`. The page imports only the two types (`import type`), so worker code never reaches the main bundle [src/drift/useDriftAlignment.ts:runAttempt]

## Invariants

- Messages whose `type` is not `'start'` are ignored [src/workers/driftAlign.worker.ts:onmessage]
- Each `start` runs one `runDriftAlignment` with no `AbortSignal`, so only `terminate()` can stop it. The hook makes one worker per attempt and terminates it after `done` or `error`. It does not terminate the worker when the reader unmounts or playback moves on [src/drift/useDriftAlignment.ts:runAttempt]
- `progress` is posted after each further 1 MiB read (`1 << 20`). One last `progress` follows the end of the stream; its `totalBytes` falls back to the bytes read when there is no `Content-Length` [src/drift/run.ts:runDriftAlignment]
- One `done` per run. Failures (HTTP status, corrupt track, CORS, codec) arrive as a `DriftReport.outcome`, not as a thrown error. The report is structured-cloned, including its `blocks` `Uint32Array` [src/drift/run.ts:runDriftAlignment]
- The worker keeps no state between messages or between workers [src/workers/driftAlign.worker.ts:onmessage]
- Known issue: the promise from `runDriftAlignment` has `.then` but no `.catch` (src/workers/driftAlign.worker.ts:18). If it ever rejects, no `done` is posted. An unhandled rejection in a worker does not fire the `error` event on the page's `Worker` object, so the attempt stays `running` for the life of the page. Each held start then waits for the engine's 10 s cap (`TIMELINE_HOLD_MAX_MS`) [src/workers/driftAlign.worker.ts:onmessage]

## Side Effects

- `GET /api/podcasts/{podcastSlug}/episodes/{episodeSlug}/sync-track/{generation}` with credentials, then a full `GET` of the audio URL (`mode: 'cors'`, `credentials: 'omit'`). Both go out from the worker's own context, through `runDriftAlignment` [src/drift/run.ts:runDriftAlignment]
- `public/sw.js` lets these fetches through untouched, because their destination is not `audio` or `video`
- `postMessage` of `progress` and `done` messages to the page [src/workers/driftAlign.worker.ts:onmessage]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
