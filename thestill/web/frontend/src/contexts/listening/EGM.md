# contexts/listening

## Overview

The spec #90 listening-progress layer behind `PlayerContext`: a framework-free `ProgressWriter` that sends ordered progress writes, an in-memory listening cache bridged to React Query, a per-tab `sessionStorage` snapshot for instant restore on reload, and the resume / restore decision helpers. `usePlaybackPersistence` wires them to the player through a stable `PlayerPort`.

## Interface

- `usePlaybackPersistence`, `PlayerPort`, `PlaybackPersistence` — the player's persistence hook [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]
- `ProgressWriter`, `ProgressWriterOptions`, `Sample`, `Hold`, `FlushEvent`, `MarkEvent` — session-based progress writer [src/contexts/listening/progressWriter.ts:ProgressWriter]
- `listeningCache`, `listeningWrites`, `newerSummary`, `ListeningWrite` — synchronous summary cache and acknowledged-write emitter [src/contexts/listening/listeningCache.ts:listeningCache]
- `readSnapshot`, `writeSnapshot`, `clearSnapshot`, `SessionSnapshot`, `PendingWrite`, `PLAYER_SESSION_STORAGE_KEY`, `LOCAL_USER_KEY` — tab snapshot [src/contexts/listening/sessionSnapshot.ts:readSnapshot]
- `planRestore`, `RestorePlan`, `SNAPSHOT_MAX_AGE_MS` (30 min), `AUTOPLAY_MAX_AGE_MS` (2 min) [src/contexts/listening/restore.ts:planRestore]
- `resolveResume`, `resumablePosition`, `ResumeDecision` [src/contexts/listening/resume.ts:resolveResume]

## Invariants

- One session per track start: random `session_id` plus a growing `seq`, so the server orders writes without trusting the client clock [src/contexts/listening/progressWriter.ts:ProgressWriter]
- Ticks go out at most every 15 s of playback (`TICK_INTERVAL_MS`), seek writes are debounced 1 s (`SEEK_DEBOUNCE_MS`), pause/ended flush immediately [src/contexts/listening/progressWriter.ts:TICK_INTERVAL_MS]
- A held session writes nothing until its resume/restore seek lands; an unknown target is never written [src/contexts/listening/progressWriter.ts:Hold]
- Writes to one episode are chained in order, so a manual mark is never overtaken by an in-flight tick [src/contexts/listening/progressWriter.ts:ProgressWriter]
- Leaving a track records it only when this tab is playing it; a paused tab never writes [src/contexts/listening/progressWriter.ts:ProgressWriter]
- Cache ordering compares server `updated_at`, never client clocks; a null ("never played") only fills an unknown entry and never erases a position [src/contexts/listening/listeningCache.ts:newerSummary]
- The snapshot is in `sessionStorage` (tab facts), owned by one user, restored only by that user; a user change clears the cache and the snapshot [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]
- An explicit `startAt` always wins; `restart` and completed episodes start at the top; positions under `MIN_RESUME_SECONDS` (2) are not resumable [src/contexts/listening/resume.ts:resolveResume]
- Restore: tab snapshot first (no network), else the server's latest unfinished episode, paused; a user action before it lands wins; failures are never an error state [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]
- `port` must be stable (memoized over refs); it is captured once [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]

## Side Effects

- `PUT /api/me/listening/{episodeId}` (with `keepalive` on page hide); a 404 drops a stale snapshot [src/api/listening.ts:putListening]
- `GET /api/me/listening/latest` for restore on load [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]
- `sessionStorage` key `thestill:player:session` [src/contexts/listening/sessionSnapshot.ts:PLAYER_SESSION_STORAGE_KEY]
- `pagehide` and `visibilitychange` listeners flush a keepalive write while playing [src/contexts/listening/usePlaybackPersistence.ts:usePlaybackPersistence]
- Seek debounce timer [src/contexts/listening/progressWriter.ts:ProgressWriter]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
