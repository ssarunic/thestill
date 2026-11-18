# utils

## Overview

Pure helpers and small browser-API wrappers shared across the app: formatting (dates, durations, analytics, briefings, admin audit), binary searches for the active transcript segment / karaoke word, entity and speaker colours, safe sign-in redirects, HTML sanitization, ⌘K search operators, playback-rate preference, MediaSession, lazy-chunk reload, and image fallbacks.

## Interface

- Formatting: `formatWhen`, `describeAuditEntry` [src/utils/adminFormat.ts:describeAuditEntry]; `RANGES`, `DEFAULT_RANGE`, `SMALL_GROUP`, `parseRange`, `sourceLabel`, `formatPct`, `formatHours`, `formatRatio`, `isSmallGroup`, `formatWeek` [src/utils/analyticsFormat.ts:SMALL_GROUP]; `describeShows`, `editionTitle`, `formatUpcomingSlot` [src/utils/briefingFormat.ts:editionTitle]; `formatEyebrowDate`, `formatPublished`, `formatLength`, `formatMinutes`, `languageName`, `hostOf`, `episodeNumberLabel`, `episodeTypeLabel` [src/utils/episodeFormat.ts:formatPublished]; `formatClock` [src/utils/formatClock.ts:formatClock]; `listeningState`, `timeLeftLabel`, `progressFraction` [src/utils/listeningFormat.ts:listeningState]; `formatDeliveredDate`, `deriveProgress`, `arrivingProgress`, `SAVED_VIEW_HREF` [src/utils/inbox.ts:deriveProgress]
- Playback timing: `findActiveIndex` [src/utils/findActiveIndex.ts:findActiveIndex], `findActiveSegmentIndex` [src/utils/transcriptSearch.ts:findActiveSegmentIndex], `findActiveWordIndex` [src/utils/wordSearch.ts:findActiveWordIndex], `HIGHLIGHT_LEAD_SECONDS` [src/utils/highlightLead.ts:HIGHLIGHT_LEAD_SECONDS]
- Player: `buildPlayerTrack`, `TrackSource` [src/utils/playerTrack.ts:buildPlayerTrack]; `elementAudioUrl`, `isSafariFamily`, `AudioOwner` [src/utils/elementAudioUrl.ts:elementAudioUrl]; `PLAYER_RATE_STORAGE_KEY`, `readPersistedRate`, `writePersistedRate`, `clampRateToAvailable`, `formatRateLabel`, `nextRate` [src/utils/playbackRate.ts:nextRate]; `setMediaSessionMetadata`, `setMediaSessionPlaybackState`, `setMediaSessionActionHandlers`, `updateMediaSessionPositionState` [src/utils/mediaSession.ts:setMediaSessionActionHandlers]
- Entities / speakers: `ENTITY_STYLES`, `entityStyle`, `entitySlug`, `entityHref`, `INLINE_HIGHLIGHT_CONFIDENCE_FLOOR` [src/utils/entityColors.ts:entityHref]; `selectTopEntities` [src/utils/mentionDensity.ts:selectTopEntities]; `buildSpeakerColorMap`, `resolveSpeakerColor`, `NEUTRAL_SPEAKER_COLOR` [src/utils/speakerColors.ts:buildSpeakerColorMap]; `STATE_BADGE_COLOR`, `STATE_LABEL` [src/utils/stateColors.ts:STATE_LABEL]
- Navigation / sign-in: `safeNextPath` [src/utils/safeNext.ts:safeNextPath]; `signInPath`, `parseSignInFragment`, `parseIntent`, `followSlugFromNext`, `notNowPath`, `rememberPendingFollow`, `consumePendingFollow`, `SignInIntent` [src/utils/signIn.ts:signInPath]; `parseCitationId` [src/utils/citationHref.ts:parseCitationId]; `groupByEpisode` [src/utils/groupByEpisode.ts:groupByEpisode]
- Content safety / rendering: `sanitizeUntrustedHtml` [src/utils/sanitize.ts:sanitizeUntrustedHtml]; `escapeListItemLeadingNumbers` [src/utils/summaryMarkdown.ts:escapeListItemLeadingNumbers]; `fillLegalPlaceholders`, `LegalOperator` [src/utils/legalPlaceholders.ts:fillLegalPlaceholders]; `parseQuery`, `ParsedQuery`, `ParsedFilters` [src/utils/searchOperators.ts:parseQuery]
- Loading: `preloadable`, `Preloadable` [src/utils/preloadable.ts:preloadable]; `isChunkLoadError`, `reloadForStaleChunk`, `installChunkReloadHandler` [src/utils/chunkReload.ts:installChunkReloadHandler]; `expandImageCandidates` [src/utils/imageFallback.ts:expandImageCandidates]; `trapTabKey` [src/utils/focusTrap.ts:trapTabKey]; `flagFor` [src/utils/regions.ts:flagFor]

## Invariants

- `safeNextPath` accepts only a same-origin path: one leading `/` (not `//` or `/\`), ≤ 512 chars, no control characters or backslashes, never `/login` or `/api/`; mirrors the server's `safe_next_path` [src/utils/safeNext.ts:safeNextPath]
- Sign-in links are the bare `/login` with validated `next` and `intent` in the fragment, never the query [src/utils/signIn.ts:signInPath]
- A pending follow is honoured only for the same slug within 15 minutes, and reading it always clears it [src/utils/signIn.ts:consumePendingFollow]
- `followSlugFromNext` names the show a Follow on `next` is for: a show page `/podcasts/<slug>` or, since spec #117, one of its episodes `/podcasts/<slug>/episodes/<e>`; anything else follows nothing [src/utils/signIn.ts:followSlugFromNext]
- `elementAudioUrl` swaps an episode's own `http://` enclosure for `/api/podcasts/{p}/episodes/{e}/audio` (a server 302 over https) only on an https page in a Safari-family browser (WebKit without Chrome/Chromium/Edg/Android, i.e. Safari and every iOS browser); any other URL, browser or protocol, or a missing slug, is returned unchanged; slugs are `encodeURIComponent`-ed [src/utils/elementAudioUrl.ts:elementAudioUrl]
- Untrusted HTML keeps only `ALLOWED_TAGS` (`p br strong b em i a ul ol li`) and `href/target/rel`; every `<a>` is forced to `target="_blank" rel="noopener noreferrer"` [src/utils/sanitize.ts:sanitizeUntrustedHtml]
- The chunk reload is guarded by a 30 s `sessionStorage` stamp to stop reload loops; without `sessionStorage` it never reloads [src/utils/chunkReload.ts:reloadForStaleChunk]
- Persisted playback rates outside 0.25–4 are ignored (default 1) [src/utils/playbackRate.ts:readPersistedRate]
- Segment search tolerance is 0.75 s; word search tolerance is 0.15 s; both add the playback offset [src/utils/transcriptSearch.ts:findActiveSegmentIndex]
- Analytics groups under `SMALL_GROUP` (5) are greyed, not hidden [src/utils/analyticsFormat.ts:SMALL_GROUP]
- Speaker colours are assigned by first appearance; callers must pass only rendered speaker rows so palettes match across surfaces [src/utils/speakerColors.ts:buildSpeakerColorMap]
- Each MediaSession action handler is registered in its own try/catch so one unsupported action does not disable the rest [src/utils/mediaSession.ts:setMediaSessionActionHandlers]
- Search operators (`person:`, `company:`, `topic:`, `podcast:`, `after:`, `before:`) are parsed client-side; quoted phrases are kept as text [src/utils/searchOperators.ts:parseQuery]
- Unknown legal placeholder keys stay visible as `{{key}}` [src/utils/legalPlaceholders.ts:fillLegalPlaceholders]

## Side Effects

- `sessionStorage` `thestill:chunk-reload-at`, `window.location.reload()`, and a `vite:preloadError` listener [src/utils/chunkReload.ts:installChunkReloadHandler]
- `sessionStorage` `thestill.pendingFollow` [src/utils/signIn.ts:rememberPendingFollow]
- `localStorage` `thestill:player:rate` [src/utils/playbackRate.ts:writePersistedRate]
- `navigator.mediaSession` metadata, playback state, position state and action handlers [src/utils/mediaSession.ts:setMediaSessionMetadata]
- Everything else is pure.

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
