# hooks

## Overview

React hooks shared by pages and components: TanStack Query wrappers over `src/api` (`useApi.ts` for the signed-in app, `useAdmin.ts` for the console, `usePublicApi.ts` for public pages, `useTranscriptEditor.ts` for spec #110 edits, `useListening.ts` for spec #90 progress), plus navigation / scroll / deep-link / UI-behaviour hooks that encode the app's navigation invariants.

## Interface

- `useApi.ts` — app data hooks [src/hooks/useApi.ts:useEpisode]
  - Dashboard: `useDashboardStats`, `useNarrationDashboardStats`, `useAnalytics`, `useRecentActivityInfinite` [src/hooks/useApi.ts:useDashboardStats]
  - Podcasts / follows: `usePodcasts`, `usePodcastsInfinite`, `useTopPodcasts`, `usePodcast`, `usePodcastEpisodesInfinite`, `useFollowPodcast`, `useUnfollowPodcast`, `useInvalidateEpisodesWhenRefreshSettles` [src/hooks/useApi.ts:usePodcast]
  - Episode reader: `useEpisode`, `useEpisodeTranscript`, `useEpisodeQuoteShares` (spec #114; no retry, errors mean no icons; refetched when the episode's state or summary changes and when `resolve-entities` completes), `useEpisodeTranscriptWords`, `useEpisodeSummary`, `useEpisodeLiveRefresh`, `useEntityBranchRefresh` [src/hooks/useApi.ts:useEpisodeLiveRefresh]
  - Pipeline / queue: `useRefreshStatus`, `useStartRefresh`, `useQueuePipelineTask`, `useEpisodeTasks`, `useAllEpisodesInfinite`, `useBulkProcess`, `useDLQTasks`, `useRetryDLQTask`, `useSkipDLQTask`, `useRetryAllDLQTasks`, `useQueueTasks`, `useProcessingStageByEpisodeId`, `useBumpQueueTask`, `useCancelQueueTask`, `useRetryFailedEpisode`, `useReprocessEpisode`, `useRunPipeline`, `useCancelPipeline` [src/hooks/useApi.ts:useEpisodeTasks]
  - Search / entities: `useQuickSearch`, `useCorpusSearch`, `useEpisodeEntities`, `useRelatedEpisodes`, `useEntitySummary`, `useEntityEpisodeMentions` [src/hooks/useApi.ts:useCorpusSearch]
  - Inbox: `useInboxInfinite`, `useInboxEntry`, `useSendToInbox`, `useSetInboxState`, `useArrivingSoon`, `useMarkInboxReadOnView` [src/hooks/useApi.ts:useInboxInfinite]
  - Briefings / narration: `useLatestBriefing`, `useGenerateBriefingNow`, `useBriefingsInfinite`, `useBriefing`, `useBriefingScript`, `useBriefingEpisodes`, `useMarkBriefingListened`, `useMarkBriefingOpened`, `useNarration`, `useNarrateBriefing` [src/hooks/useApi.ts:useLatestBriefing]
  - MCP / OAuth: `useMcpToken`, `useCreateOrRotateMcpToken`, `useRevokeMcpToken`, `useOAuthPending`, `useOAuthGrants`, `useDisconnectOAuthGrant` [src/hooks/useApi.ts:useMcpToken]
- `useAdminUsers`, `useAdminUser`, `useAdminSettings`, `useAdminAudit`, `useSetUserStatus`, `useLogoutEverywhere`, `useRemoveIntroducedFeeds`, `useSetQuotaOverrides`, `useSetAdminRole`, `usePutAdminSetting`, `useResetAdminSetting`, `useSendTestAlert` [src/hooks/useAdmin.ts:useAdminMutation]; `useAdminToast` [src/hooks/useAdminToast.ts:useAdminToast]
- `usePublicPodcast`, `usePublicPodcastEpisodes`, `usePublicEpisode`, `usePublicSummary`, `usePublicEpisodeQuotes`, `usePublicQuoteShare` (spec #114; disabled without an id), `usePublicRecentSummaries`, `usePublicTopChart`, `usePublicEntity`, `usePublicEntityQuotes`, `usePublicWhoSaidWhat` [src/hooks/usePublicApi.ts:usePublicEpisode]
- `useTranscriptEditor`, `useTranscriptEditorState`, `editorStateKey`, `segmentAnchor`, `SaveStatus` [src/hooks/useTranscriptEditor.ts:useTranscriptEditor]; `undoRedoTargets` [src/hooks/transcriptEditorTargets.ts:undoRedoTargets]
- `useResolvedListening`, `ListeningQuerySync`, `useInProgressListening` [src/hooks/useListening.ts:ListeningQuerySync]; `usePlayerRatePreference` [src/hooks/usePlayerRatePreference.ts:usePlayerRatePreference]
- Navigation / scroll: `useScrollRestoration` [src/hooks/useScrollRestoration.ts:useScrollRestoration], `useReadingPosition` [src/hooks/useReadingPosition.ts:useReadingPosition], `useUrlParams` [src/hooks/useUrlParams.ts:useUrlParams], `useDebouncedSearchParam` [src/hooks/useDebouncedSearchParam.ts:useDebouncedSearchParam], `useBackgroundLocation`, `useIsNavActive` [src/hooks/useBackgroundLocation.ts:useBackgroundLocation], `useEpisodeLinkState` [src/hooks/useEpisodeLinkState.ts:useEpisodeLinkState], `useArtworkHint` [src/hooks/useArtworkHint.ts:useArtworkHint]
- Deep links: `useDeepLinkSeek(key, onSeek, ready, onCue?, entryKey?)`, `deepLinkParam`, `deepLinkSeconds`, `hasUserGesture`, `settleDeepLinkCue`, `buildTimestampDeepLink`, `episodeTimestampPath`, `__resetDeepLinkSeekForTests` [src/hooks/useDeepLinkSeek.ts:episodeTimestampPath]; `useDeepLinkScrollTarget`, `deepLinkSegmentIndex` [src/hooks/useDeepLinkScrollTarget.ts:useDeepLinkScrollTarget]
- Transcript follow / karaoke: `useAutoScrollFollow`, `usePersistedBoolean` [src/hooks/useAutoScrollFollow.ts:useAutoScrollFollow], `useFollowPlayback`, `FOLLOW_PLAYBACK_STORAGE_KEY` [src/hooks/useFollowPlayback.ts:useFollowPlayback], `useKaraokeActiveWordIdx` [src/hooks/useKaraokeActiveWordIdx.ts:useKaraokeActiveWordIdx]
- UI behaviour: `useBodyScrollLock` [src/hooks/useBodyScrollLock.ts:useBodyScrollLock], `useCanScrollDown` [src/hooks/useCanScrollDown.ts:useCanScrollDown], `useCollapsingHeader` [src/hooks/useCollapsingHeader.ts:useCollapsingHeader], `useDismissablePopover` [src/hooks/useDismissablePopover.ts:useDismissablePopover], `useMediaQuery`, `useIsSmUp` [src/hooks/useMediaQuery.ts:useIsSmUp], `useSwipeDownToClose` [src/hooks/useSwipeDownToClose.ts:useSwipeDownToClose], `useShareAction` [src/hooks/useShareAction.ts:useShareAction]
- Usage events: `useReaderEvents` [src/hooks/useReaderEvents.ts:useReaderEvents], `useReportBriefingOpened` [src/hooks/useReportBriefingOpened.ts:useReportBriefingOpened]

## Invariants

- Query keys are hierarchical arrays (`['episodes', podcastSlug, episodeSlug, 'transcript']`, `['episodes', 'tasks', id]`, `['inbox', …]`, `['dlq', …]`, `['queue', …]`, `['admin', …]`); mutations invalidate by prefix [src/hooks/useApi.ts:useEpisode]
- `useEpisode` is the reader's clock: polls every 5 s (also in background tabs) while `live`, stops once settled; it is not gated on task activity [src/hooks/useApi.ts:useEpisode]
- Transcript / summary / words queries do not poll (`refetchInterval: false`); `useEpisodeLiveRefresh` reconciles them against the episode level rather than an edge [src/hooks/useApi.ts:useEpisodeLiveRefresh]
- `useEpisodeTasks` polls 2 s while a task is active (`retry_scheduled` counts), 15 s for `IDLE_TICKS_BEFORE_BACKOFF` (4) quiet ticks, then stops only for a content-terminal episode; others keep a 60 s probe [src/hooks/useApi.ts:useEpisodeTasks]
- `useMarkInboxReadOnView` marks the inbox row read once per viewed episode, only after a summary is available; fire-and-forget [src/hooks/useApi.ts:useMarkInboxReadOnView]
- Every admin write invalidates the whole `['admin']` key [src/hooks/useAdmin.ts:useAdminMutation]
- A public 404 (`PublicNotFound`) is final and never retried; public queries use `publicKeys` and 5 min `staleTime` [src/hooks/usePublicApi.ts:retry]
- Transcript edits carry `base_seq` and a per-mount `session_id`; a stale refusal reloads and sets `stale`; every error reloads so `base_seq` never drifts [src/hooks/useTranscriptEditor.ts:useTranscriptEditor]
- `useTranscriptEditorState` is gated by the caller holding `transcript.edit`, so readers never fetch it [src/hooks/useTranscriptEditor.ts:useTranscriptEditorState]
- List filters, sort and page live in the URL (replace navigation); changing any filter resets to page one [src/hooks/useUrlParams.ts:useUrlParams]
- Window scroll: PUSH to a new pathname goes to the top, POP restores the entry's offset, same-pathname changes leave it; mounted once per app and never under a `<Routes location>`; inert while an overlay background location exists [src/hooks/useScrollRestoration.ts:useScrollRestoration]
- Reader position is saved as a percentage and expires after 30 days. In page mode (window scroll) router POP is left to `useScrollRestoration`; this hook restores only on a document `back_forward` load or `location.state.restoreScroll`. In overlay mode (own scroll container) it restores on a router POP to an entry it has already seen, a document `back_forward` load, or `restoreScroll`, and otherwise scrolls the container to the top [src/hooks/useReadingPosition.ts:useReadingPosition]
- Deep-link `t` / `about` are read from the query string, falling back to the fragment; `episodeTimestampPath` builds `?t=` in whole seconds, but not every link uses it: src/pages/SearchResults.tsx:375 and src/components/NowPlayingSheet.tsx:324 build `?t=` by hand [src/hooks/useDeepLinkSeek.ts:episodeTimestampPath]
- `deepLinkSeconds` accepts only a finite `t` ≥ 0; `useDeepLinkSeek` acts once per (episode, history entry): it calls `onSeek` only when `hasUserGesture()` (a cold shared link cannot start audio), always offers the moment to `onCue`, and on a return to the entry only re-offers a cue not yet settled, never seeking again [src/hooks/useDeepLinkSeek.ts:useDeepLinkSeek]
- `deepLinkSegmentIndex` lands a moment in a gap (or past the end) on the last segment that had started; -1 before the first [src/hooks/useDeepLinkScrollTarget.ts:deepLinkSegmentIndex]
- Every scroll `useAutoScrollFollow` makes (follow step, Resume, `scrollToKey`) goes through one aim: a jump further than 3 viewports is `instant` rather than smooth, then the row is re-aimed (60 ms polls, at most 10 corrections) until it is really on screen; the newest aim cancels an older one, and the visitor's wheel, touch or scroll keys cancel it [src/hooks/useAutoScrollFollow.ts:useAutoScrollFollow]
- While an overlay is open, nav highlighting uses the background location [src/hooks/useBackgroundLocation.ts:useIsNavActive]
- Nested body scroll locks compose: each restores the `overflow` it found [src/hooks/useBodyScrollLock.ts:useBodyScrollLock]
- `share` usage events carry the kind, never the URL [src/hooks/useShareAction.ts:useShareAction]

## Side Effects

- All app network I/O via `src/api` (see `src/api/EGM.md`), with polling intervals (episode 5 s, podcast 5 s while `refresh_pending`, DLQ 10 s, queue 5 s active / 15 s idle, refresh 1 s running / 15 s idle, arriving 30 s while non-empty, latest briefing 15 s while items are pending) [src/hooks/useApi.ts:useDLQTasks]
- `localStorage`: `reading-position-<episodeId>` [src/hooks/useReadingPosition.ts:STORAGE_PREFIX], `thestill:transcript:followPlayback` [src/hooks/useFollowPlayback.ts:FOLLOW_PLAYBACK_STORAGE_KEY], `usePersistedBoolean` keys [src/hooks/useAutoScrollFollow.ts:usePersistedBoolean]
- `window.scrollTo` / container `scrollTo` and scroll listeners [src/hooks/useScrollRestoration.ts:useScrollRestoration]; `scrollIntoView`, a settle timer and window `wheel` / `touchmove` / `keydown` listeners per jump [src/hooks/useAutoScrollFollow.ts:useAutoScrollFollow]
- A module-level map of deep-link cues per history entry [src/hooks/useDeepLinkSeek.ts:useDeepLinkSeek]
- `document.body.style.overflow` [src/hooks/useBodyScrollLock.ts:useBodyScrollLock]
- `navigator.share` / `navigator.clipboard.writeText` and `track('share')` [src/hooks/useShareAction.ts:useShareAction]
- `track('reader_tab' | 'read_depth')` [src/hooks/useReaderEvents.ts:useReaderEvents]; `POST /api/briefings/{id}/opened` once per id [src/hooks/useReportBriefingOpened.ts:useReportBriefingOpened]
- Listening writes invalidate `['listening']`, and completions `['inbox']` [src/hooks/useListening.ts:ListeningQuerySync]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
