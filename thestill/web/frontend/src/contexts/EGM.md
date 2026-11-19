# contexts

## Overview

App-wide React providers: `AuthProvider` (session, multi-user mode, admin flag, named permissions, analytics key) and `PlayerProvider` (spec #61 unified audio/video playback session over one stable `<video>` element plus the YouTube engine). The player delegates engines to `./playback-engine` and progress persistence to `./listening`.

## Interface

- `AuthProvider`, `useAuth`, `useOptionalAuth`, `User`, `Permission` (`'transcript.edit' | 'drift.align'`) — auth context: `user`, `isLoading`, `isMultiUser`, `isAuthenticated`, `isAnonymous`, `isAdmin`, `can()`, `emailDeliveryAvailable`, `usageAnalyticsAvailable`, `analyticsKey`, `productTipsEmailAvailable`, `accountDisabled`, `adminContactEmail`, `login`, `logout`, `refreshAuth`, `updateRegion`, `updateUsageAnalytics`, `updateProductTipsEmail` [src/contexts/AuthContext.tsx:AuthProvider]
- `PlayerProvider`, `usePlayer`, `usePlayerTime` — player context: `track`, `isPlaying`, `play`, `pause`, `resume`, `toggle`, `seek`, `skip`, `setRate`, `stop`, `markListened`, `isCurrent`, `getCurrentTime`, `presentation`, `switchRendition`, `playYouTube`, `registerTheaterSlot`, `registerFloatingSlot`, `requestPip`, `setVolume`, `toggleMute`, `mediaError`, and the spec #116 timeline surface `applyTimeline`, `timelineApplied`, `timelineMap`, `listenerAd`, `getAssetTime`, `getAssetDuration`, `timelineHold`, `releaseTimelineHold`, `reloadHeldForCheck`; `seek(seconds, { transcript })` … [src/contexts/PlayerContext.tsx:PlayerContextValue]
- `LOAD_PATIENCE_MS` (15 000) — how long a press on Play waits for any audio before reporting it could not load [src/contexts/PlayerContext.tsx:LOAD_PATIENCE_MS]
- `PlayerTrack`, `PlayOptions`, `MediaKind`, `VideoPreference`, `RenditionKind`, `EngineKind` — player types [src/contexts/PlayerContext.tsx:PlayerTrack]
- Subdirectory [listening](./listening/EGM.md) — spec #90 progress writer, listening cache, tab snapshot, resume/restore
- Subdirectory [playback-engine](./playback-engine/EGM.md) — `PlaybackEngine` contract, `NativeEngine` (HLS via hls.js, spec #116 timeline and hold), `TimelineMap`, `YouTubeEngine`

## Invariants

- `isAdmin` is always true in single-user mode; in multi-user mode it is the server's `is_admin` flag [src/contexts/AuthContext.tsx:isAdmin]
- Permissions come from the server's list; `can()` never derives one from `isAdmin`, so entry points check `can(permission)` [src/contexts/AuthContext.tsx:can]
- `isAnonymous` is true only after loading, in multi-user mode, signed out, with public pages on [src/contexts/AuthContext.tsx:isAnonymous]
- `initialViewer="anonymous"` (spec #105 prerender) renders the public site from the first frame; `/api/auth/status` then confirms it [src/contexts/AuthContext.tsx:AuthProvider]
- `login(next)` passes `next` through `safeNextPath` before redirecting; the server validates it again [src/contexts/AuthContext.tsx:login]
- The `<video>` element is created once and never reparented; exactly one engine is active and each engine's events are ignored while the other is active [src/contexts/PlayerContext.tsx:PlayerProvider]
- Play/pause/position/rate belong to the session and are never affected by presentation (theater / floating / PiP) changes [src/contexts/PlayerContext.tsx:PlayerProvider]
- `PlayOptions.startAt` is on the asset's timeline; callers already fold in the playback offset, so `play()` must not add it again [src/contexts/PlayerContext.tsx:PlayOptions]
- New and restored tracks always start on the native engine; YouTube is opt-in per episode, never a sticky default [src/contexts/PlayerContext.tsx:PlayerProvider]
- Spec #62 §7 compliance: the YouTube iframe may only play while visibly presented; leaving presentation switches to the `audio` rendition [src/contexts/PlayerContext.tsx:PlayerProvider]
- Every press on Play is one load attempt: `isLoading` turns on, and if nothing of the audio has arrived after `LOAD_PATIENCE_MS` the native engine is paused and `mediaError` becomes `AUDIO_NOT_LOADED_MESSAGE`; a refusal of a superseded attempt never touches the newer one; a pause ends the wait [src/contexts/PlayerContext.tsx:awaitAudio]
- The exposed `isLoading` is `(isLoading && isPlaying) || timelineHold`, so a held start shows the same spinner and a refused start never spins; `toggle` on a spinning start withdraws it [src/contexts/PlayerContext.tsx:PlayerProvider]
- Only the audio rendition of a non-HLS source may load with `crossorigin` or hold for a map; the audio rendition loads through `elementAudioUrl` (an `http` enclosure in Safari on an https page goes via `/api/podcasts/{p}/episodes/{e}/audio`) [src/contexts/PlayerContext.tsx:beginSource]
- Spec #116: a start at an explicit `startAt`, or `seek(t, { transcript: true })`, is held silent for its map when `isTimelineExpected(episodeId)` and no map is in force; any new source lifts the map [src/contexts/PlayerContext.tsx:shouldHoldFor]
- `applyTimeline` is refused (false) unless `episodeId` and `audioUrl` are what the native engine is playing; setting a map also needs the native engine and the audio rendition, lifting (null) does not; `timelineMap` and `listenerAd` are null on the YouTube engine [src/contexts/PlayerContext.tsx:applyTimeline]
- Saved listening positions are element time (`getElementTime`), not transcript time, so a resume (which seeks before any map exists) lands in the same place in an unchanged copy [src/contexts/PlayerContext.tsx:port]
- PiP state follows `enterpictureinpicture` / `leavepictureinpicture` events, never assumptions [src/contexts/PlayerContext.tsx:PlayerProvider]
- The persisted rate is the user's explicit choice; an engine snapping to a supported rate does not overwrite it [src/hooks/usePlayerRatePreference.ts:usePlayerRatePreference]

## Side Effects

- `GET /api/auth/status` on mount, `PATCH /api/auth/me`, `POST /api/auth/logout` (then `resetAnalytics()` and, in multi-user mode, redirect to `/login`) [src/contexts/AuthContext.tsx:AuthProvider]
- `login` navigates to `/api/auth/google/login?next=…` [src/contexts/AuthContext.tsx:login]
- MediaSession metadata, playback state, position and action handlers [src/contexts/PlayerContext.tsx:PlayerProvider]
- `localStorage` `thestill:player:rate` via `usePlayerRatePreference` [src/utils/playbackRate.ts:PLAYER_RATE_STORAGE_KEY]
- Progress writes and restore through `usePlaybackPersistence` (`/api/me/listening/*`) [src/contexts/PlayerContext.tsx:PlayerProvider]
- Media-layer rAF positioning loop while a slot is active [src/contexts/PlayerContext.tsx:PlayerProvider]
- `LOAD_PATIENCE_MS` load-watch timer per play attempt [src/contexts/PlayerContext.tsx:armLoadWatch]
- Each native load asks `beginAudioLoad` (`src/drift/corsVerdict.ts`), which records the load in memory and allows `crossorigin` only with the `drift.align` switch on and a stored `cors_ok` verdict for the URL [src/contexts/PlayerContext.tsx:beginSource]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
