# api

## Overview

The frontend's HTTP layer: `fetch`-based clients for the FastAPI backend under `/api` (signed-in app, admin console, transcript editor, listening progress, public pages) plus the shared response/request types. Calls are same-origin, so the session cookie always travels; most set `credentials: 'include'` explicitly. React Query hooks in `src/hooks/` wrap these functions.

## Interface

- `client.ts` — main app client over `API_BASE = '/api'` [src/api/client.ts:API_BASE]
  - `getDashboardStats`, `getNarrationDashboardStats`, `getAnalytics`, `getRecentActivity` — `GET /api/dashboard/{stats,narration,analytics,activity}` [src/api/client.ts:getDashboardStats]
  - `getPodcasts`, `getTopPodcasts`, `getPodcast`, `resolvePodcast`, `followPodcast`, `unfollowPodcast`, `getPodcastEpisodes` — `/api/podcasts*`, `/api/top-podcasts`, `POST /api/podcasts/resolve`, `POST|DELETE /api/podcasts/{slug}/follow` [src/api/client.ts:getPodcast]
  - `reportDriftOutcome` — spec #116 `POST /api/podcasts/{p}/episodes/{e}/drift-outcome` with a `DriftOutcomeBody` (from `src/drift/report.ts`); throws on a non-2xx, callers fire and forget [src/api/client.ts:reportDriftOutcome]
  - `getEpisode`, `getEpisodeTranscript`, `getEpisodeSummary`, `getEpisodeTranscriptWords` — `/api/podcasts/{p}/episodes/{e}[/transcript|/summary|/transcript/words]` [src/api/client.ts:getEpisode]
  - `startRefresh`, `getRefreshStatus`, `addPodcast`, `getAddPodcastStatus`, `queuePipelineTask`, `getEpisodeTasks`, `runPipeline`, `cancelPipeline` — `/api/commands/{refresh,add,<stage>,run-pipeline,episode/{id}/tasks|cancel-pipeline}` [src/api/client.ts:queuePipelineTask]
  - `getAllEpisodes`, `bulkProcessEpisodes`, `retryFailedEpisode`, `reprocessEpisode` — `/api/episodes`, `/api/episodes/bulk/process`, `/api/episodes/{id}/{retry,reprocess}` [src/api/client.ts:reprocessEpisode]
  - `getDLQTasks`, `retryDLQTask`, `skipDLQTask`, `retryAllDLQTasks`, `getQueueTasks`, `bumpQueueTask`, `cancelQueueTask` — `/api/commands/dlq*`, `/api/commands/queue/*` [src/api/client.ts:getDLQTasks]
  - `quickSearch`, `corpusSearch`, `getRelatedEpisodes` — `/api/search/{quick,corpus,related}` [src/api/client.ts:corpusSearch]
  - `getEpisodeEntities`, `getEntitySummary`, `getEntityEpisodeMentions` — `/api/episodes/{id}/entities`, `/api/entities/{type}/{id}[/mentions]` [src/api/client.ts:getEntitySummary]
  - `getInbox`, `markInboxRead`, `setInboxState`, `getInboxEntry`, `sendToInbox`, `getArriving` — `/api/inbox*` [src/api/client.ts:getInbox]
  - `getLatestBriefing`, `getBriefings`, `getBriefing`, `getBriefingScript`, `getBriefingEpisodes`, `markBriefingListened`, `markBriefingOpened`, `getBriefingSchedule`, `putBriefingSchedule`, `narrateBriefing`, `getNarration`, `browserTimezone` — `/api/briefings*`, `/api/narrations/{id}` [src/api/client.ts:getBriefing]
  - `importEpisode` — `POST /api/imports` [src/api/client.ts:importEpisode]
  - `getMcpToken`, `createOrRotateMcpToken`, `revokeMcpToken`, `getOAuthPending`, `approveOAuthPending`, `denyOAuthPending`, `getOAuthGrants`, `disconnectOAuthGrant` — `/api/me/mcp-token`, `/api/oauth/pending/{id}[/approve|/deny]`, `/api/me/oauth-grants` [src/api/client.ts:getOAuthPending]
- `admin.ts` — spec #104 admin console calls under `/api/admin` (`getAdminUsers`, `getAdminUser`, `setUserStatus`, `logoutEverywhere`, `removeIntroducedFeeds`, `setQuotaOverrides`, `setAdminRole`, `getAdminSettings`, `putAdminSetting`, `resetAdminSetting`, `sendTestAlert`, `getAdminAudit`) [src/api/admin.ts:getAdminUsers]
- `transcriptEdits.ts` — spec #110 editor calls under `/api/episodes/{id}/transcript/edits` (`getTranscriptEditorState`, `applyTranscriptEdits`, `revertTranscriptEdit`, `finishTranscriptEdits`, `saveSpeakerAsHost`, `searchTranscriptPeople`), `TranscriptEditError`, `TranscriptEditRefusal` [src/api/transcriptEdits.ts:applyTranscriptEdits]
- `listening.ts` — spec #90 progress transport under `/api/me/listening` (`putListening`, `getListening`, `getLatestListening`, `listInProgress`, `SendResult`) [src/api/listening.ts:putListening]
- `public.ts` — signed-out client under `/api/public` (`publicApi`, `publicKeys`, `PublicNotFound`, `Public*` types including `PublicSpeakerRole` (`guest|host|recurring`, optional `role` on `PublicEpisodeQuotes.mentioned`), `formatTimestamp`, `formatDate`, `entityPath`, `episodePath`) [src/api/public.ts:publicApi]
- `followPodcast.ts` — `followPodcast` (queued add-and-wait), `resetFollowQueueForTests` [src/api/followPodcast.ts:followPodcast]
- `http.ts` — `fetchJson`, `queryString` [src/api/http.ts:fetchJson]; `errors.ts` — `apiErrorMessage`, `apiErrorCode`, `redirectIfAccountDisabled`, `ApiStatusError` [src/api/errors.ts:apiErrorMessage]
- `types.ts` — shared API types by domain [src/api/types.ts:EpisodeDetail]
  - Dashboard / analytics: `DashboardStats`, `AnalyticsRange` (`7d|28d|90d`), `AnalyticsReport`, `AnalyticsScorecard`, `NarrationDashboardStats`, `ActivityItem` [src/api/types.ts:AnalyticsReport]
  - Podcasts / episodes: `PodcastSummary`, `PodcastDetailResponse`, `Episode`, `EpisodeDetail`, `PlaybackManifest`, `PlatformLink`, `TopPodcast`, `FollowSource` (`episode_page|episode_pending_follow|show_page|pending_follow|top_podcasts|add_podcast`), `AddPodcastTaskStatus`; `EpisodeDetail` carries spec #117 `podcast_is_following` and `podcast_followable` [src/api/types.ts:PlaybackManifest]
  - Transcript / summary: `TranscriptType`, `SegmentKind` (`content|filler|ad_break|music|intro|outro`), `AnnotatedSegment`, `WordTimestamp`, `TranscriptWordsResponse`, `SummaryCitation`, `ContentResponse` [src/api/types.ts:AnnotatedSegment]
  - Listening: `ListeningSummary`, `ListeningEvent` (`tick|pause|seek|ended|stop|mark_played|mark_unplayed`), `ProgressBody`, `ProgressAck`, `ResumableItem` [src/api/types.ts:ListeningEvent]
  - Pipeline / queue: `PipelineStage` (`download`, `downsample`, `transcribe`, `clean`, `summarize`, `extract-entities`, `resolve-entities`, `reindex`, `rebuild-cooccurrences`, `compute-related`, `enrich-entities`, `refresh-feed`), `ExtendedPipelineTaskStatus` (`pending|processing|completed|failed|retry_scheduled|dead`), `EpisodeState`, `DLQTask`, `DLQBranchFilter`, `QueuedTaskWithContext`, `ReprocessFromStage` (`transcribe|clean|summarize|entities`), `FailureType` [src/api/types.ts:PipelineStage]
  - Search / entities: `SearchResult`, `CorpusSearchOptions`, `QuickSearchResponse`, `RelatedEpisode`, `EntityType` (`person|company|product|topic`), `SpeakerKind`, `MentionLite`, `EpisodeEntity` (optional `takes_part`: speaks on this episode), `EntitySummaryResponse` [src/api/types.ts:EpisodeEntity]
  - Inbox / briefings / imports: `InboxState` (`unread|read|saved|dismissed`), `InboxItem`, `ArrivingItem`, `Briefing`, `BriefingFrequency`, `BriefingScheduleResponse`, `ImportKind`, `ImportRequest` [src/api/types.ts:InboxItem]
  - MCP / OAuth: `McpTokenScope` (`read|follows|pipeline`), `McpTokenInfo`, `OAuthPendingResponse`, `OAuthGrant` [src/api/types.ts:McpTokenInfo]
  - Admin: `AccountStatus` (`active|suspended|banned`), `AdminUserSort`, `AdminUser`, `AdminUserDetail`, `AdminAuditEntry`, `AdminSetting` [src/api/types.ts:AdminUser]
  - Transcript editor: `TranscriptEditOpName` (`speaker.rename_label`, `speaker.merge_labels`, `segment.set_speaker`, `segment.set_text`, `segment.set_kind`, `segment.split`, `segment.merge`, `revert`), `TranscriptEditStatus`, `TranscriptEdit`, `TranscriptEditConflict`, `TranscriptEditorState`, `TranscriptSpeaker` [src/api/types.ts:TranscriptEditOpName]

## Invariants

- Requests are same-origin, so cookies travel by default; most helpers also set `credentials: 'include'`, but ten POST helpers in `client.ts` omit it (`startRefresh`, `queuePipelineTask`, `bulkProcessEpisodes`, `retryDLQTask`, `skipDLQTask`, `retryAllDLQTasks`, `retryFailedEpisode`, `reprocessEpisode`, `runPipeline`, `cancelPipeline`), as does `AuthContext.logout` (src/contexts/AuthContext.tsx:176); bodies are JSON with `Content-Type: application/json` [src/api/http.ts:fetchJson]
- Error text: FastAPI `detail` string, else joined 422 `msg`s, else an object's readable `message`/`error` (a snake_case `error` is a machine code, so `message` wins) [src/api/errors.ts:apiErrorMessage]
- An `account_disabled` refusal sends the browser to `/login?error=account_disabled` (unless already there), but only from `fetchApi` (most GETs in `client.ts`), `addPodcast`, `getOAuthPending` and `fetchJson` (used by `admin.ts`); other helpers (`sendApi`, the POSTs with their own `fetch`, `editorFetch`, the listening transport, the public client) throw without redirecting [src/api/errors.ts:redirectIfAccountDisabled]
- `ApiStatusError` keeps the HTTP status so callers can wait out a 409 [src/api/errors.ts:ApiStatusError]
- Use `followPodcast()` (not bare `addPodcast`) to follow: `POST /api/commands/add` runs one add at a time server-wide, so follows are queued per tab, a 409 is waited out (60 s), then the add is polled by `task_id` (90 s; 20 s per request) [src/api/followPodcast.ts:followPodcast]
- A 404 from `transcript/words` and from `oauth/pending/{id}` resolves to `null`, not an error [src/api/client.ts:getEpisodeTranscriptWords]
- `putListening` never throws: `ok` / `not_found` / `failed`; ordering is by `seq`, not arrival [src/api/listening.ts:putListening]
- Transcript-edit refusals are parsed into `TranscriptEditRefusal`; `isStale` (`stale_base_seq`) tells the editor to rebase [src/api/transcriptEdits.ts:TranscriptEditError]
- Public path segments are `encodeURIComponent`-ed; a public 404 throws `PublicNotFound` [src/api/public.ts:publicApi]
- Every public React Query key comes from `publicKeys`, so a server-seeded (prerendered) key is always the key the hook reads [src/api/public.ts:publicKeys]
- `PlaybackAsset.timeline_offset` maps logical (transcript) time t to asset time t + offset; the YouTube rendition has no offset and is never the default engine [src/api/types.ts:PlaybackManifest]
- Inbox delivery is immutable: `delivered_at` never changes; state is per user [src/api/types.ts:InboxState]
- The MCP token GET returns state only, never the plaintext; the connector URL is returned once on POST [src/api/types.ts:McpTokenScope]

## Side Effects

- All network I/O of the app: `/api/*`, `/api/admin/*`, `/api/me/listening/*` (with `keepalive` on page hide), `/api/public/*` [src/api/client.ts:fetchApi]
- `window.location.assign('/login?error=account_disabled')` on a disabled account [src/api/errors.ts:redirectIfAccountDisabled]
- Polling timers in the follow queue [src/api/followPodcast.ts:waitForAddTask]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
