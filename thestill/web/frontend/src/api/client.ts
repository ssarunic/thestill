import type {
  DashboardStats,
  NarrationDashboardStats,
  ActivityResponse,
  PodcastsResponse,
  PodcastDetailResponse,
  EpisodesResponse,
  EpisodeDetailResponse,
  ContentResponse,
  TranscriptWordsResponse,
  RefreshRequest,
  RefreshResponse,
  RefreshTaskStatus,
  AddPodcastRequest,
  AddPodcastResponse,
  AddPodcastTaskStatus,
  PipelineStage,
  PipelineTaskRequest,
  PipelineTaskResponse,
  PipelineTaskStatusResponse,
  EpisodeTasksResponse,
  AllEpisodesResponse,
  EpisodeFilters,
  BulkProcessResponse,
  DLQBranchFilter,
  DLQListResponse,
  DLQActionResponse,
  DLQBulkRetryResponse,
  QueueTasksResponse,
  BumpTaskResponse,
  CancelTaskResponse,
  EpisodeFailure,
  FailedEpisodesResponse,
  EpisodeRetryResponse,
  RunPipelineRequest,
  RunPipelineResponse,
  CancelPipelineResponse,
  ExtendedEpisodeTasksResponse,
  NarrateBriefingRequest,
  NarrateBriefingResponse,
  NarrationDetail,
  TopPodcastsResponse,
  ResolvePodcastRequest,
  ResolvePodcastResponse,
  CorpusSearchOptions,
  QuickSearchOptions,
  QuickSearchResponse,
  SearchResponse,
  RelatedEpisodesResponse,
  EpisodeEntitiesResponse,
  EntitySummaryResponse,
  EntityType,
  BriefingResponse,
  LatestBriefingResponse,
  BriefingsListResponse,
  BriefingScheduleResponse,
  BriefingScheduleUpdate,
  BriefingScriptResponse,
  InboxListResponse,
  InboxMarkReadResponse,
  InboxState,
  InboxStateResponse,
  InboxUnreadCountResponse,
  ImportRequest,
  ImportResponse,
} from './types'

const API_BASE = '/api'

async function fetchApi<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    credentials: 'include',
  })
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

// Dashboard API
export async function getDashboardStats(): Promise<DashboardStats> {
  return fetchApi<DashboardStats>('/dashboard/stats')
}

export async function getNarrationDashboardStats(): Promise<NarrationDashboardStats> {
  return fetchApi<NarrationDashboardStats>('/dashboard/narration')
}

export async function getRecentActivity(limit = 10, offset = 0): Promise<ActivityResponse> {
  return fetchApi<ActivityResponse>(`/dashboard/activity?limit=${limit}&offset=${offset}`)
}

// Podcasts API
export async function getPodcasts(limit = 12, offset = 0, q?: string): Promise<PodcastsResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (q) params.set('q', q)
  return fetchApi<PodcastsResponse>(`/podcasts?${params.toString()}`)
}

// Top Podcasts API
export async function getTopPodcasts(
  region?: string,
  limit = 50,
  q?: string,
  category?: string,
  signal?: AbortSignal,
): Promise<TopPodcastsResponse> {
  const params = new URLSearchParams()
  if (region) params.set('region', region)
  params.set('limit', String(limit))
  if (q) params.set('q', q)
  if (category) params.set('category', category)
  const response = await fetch(`${API_BASE}/top-podcasts?${params.toString()}`, {
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

export async function getPodcast(podcastSlug: string): Promise<PodcastDetailResponse> {
  return fetchApi<PodcastDetailResponse>(`/podcasts/${podcastSlug}`)
}

// Lazy import: resolve a podcast URL (e.g. a top-chart entry) to a local
// slug, creating the row + kicking off a background refresh if needed.
// Returns synchronously in ~1–2s; episodes populate via the detail page's
// existing 5s refetch interval.
export async function resolvePodcast(
  request: ResolvePodcastRequest,
): Promise<ResolvePodcastResponse> {
  const response = await fetch(`${API_BASE}/podcasts/resolve`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message =
      typeof error.detail === 'string'
        ? error.detail
        : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// Follow a podcast
export async function followPodcast(podcastSlug: string): Promise<void> {
  const response = await fetch(`${API_BASE}/podcasts/${podcastSlug}/follow`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || `API error: ${response.status}`)
  }
}

// Unfollow a podcast
export async function unfollowPodcast(podcastSlug: string): Promise<void> {
  const response = await fetch(`${API_BASE}/podcasts/${podcastSlug}/follow`, {
    method: 'DELETE',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail || `API error: ${response.status}`)
  }
}

export async function getPodcastEpisodes(
  podcastSlug: string,
  limit = 20,
  offset = 0
): Promise<EpisodesResponse> {
  return fetchApi<EpisodesResponse>(`/podcasts/${podcastSlug}/episodes?limit=${limit}&offset=${offset}`)
}

// Episodes API (accessed via podcast slug + episode slug)
export async function getEpisode(podcastSlug: string, episodeSlug: string): Promise<EpisodeDetailResponse> {
  return fetchApi<EpisodeDetailResponse>(`/podcasts/${podcastSlug}/episodes/${episodeSlug}`)
}

export async function getEpisodeTranscript(podcastSlug: string, episodeSlug: string): Promise<ContentResponse> {
  return fetchApi<ContentResponse>(`/podcasts/${podcastSlug}/episodes/${episodeSlug}/transcript`)
}

export async function getEpisodeSummary(
  podcastSlug: string,
  episodeSlug: string,
  lang?: string,
): Promise<ContentResponse> {
  const query = lang ? `?lang=${encodeURIComponent(lang)}` : ''
  return fetchApi<ContentResponse>(`/podcasts/${podcastSlug}/episodes/${episodeSlug}/summary${query}`)
}

// Spec #38 — karaoke wipe data source. 404 is a valid response shape
// (episode has no word-level timestamps), not an error condition, so it
// resolves to ``null`` instead of throwing. The chip in the viewer
// toolbar reads this null sentinel and renders disabled-with-tooltip.
export async function getEpisodeTranscriptWords(
  podcastSlug: string,
  episodeSlug: string,
): Promise<TranscriptWordsResponse | null> {
  const response = await fetch(
    `${API_BASE}/podcasts/${podcastSlug}/episodes/${episodeSlug}/transcript/words`,
    { credentials: 'include' },
  )
  if (response.status === 404) return null
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

// Commands API
export async function startRefresh(request: RefreshRequest = {}): Promise<RefreshResponse> {
  const response = await fetch(`${API_BASE}/commands/refresh`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail?.error || `API error: ${response.status}`)
  }

  return response.json()
}

export async function getRefreshStatus(): Promise<RefreshTaskStatus> {
  return fetchApi<RefreshTaskStatus>('/commands/refresh/status')
}

// Add Podcast API
export async function addPodcast(request: AddPodcastRequest): Promise<AddPodcastResponse> {
  const response = await fetch(`${API_BASE}/commands/add`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json()
    throw new Error(error.detail?.error || `API error: ${response.status}`)
  }

  return response.json()
}

export async function getAddPodcastStatus(): Promise<AddPodcastTaskStatus> {
  return fetchApi<AddPodcastTaskStatus>('/commands/add/status')
}

// Pipeline Task API (Queue-based)
export async function queuePipelineTask(
  stage: PipelineStage,
  request: PipelineTaskRequest
): Promise<PipelineTaskResponse> {
  const response = await fetch(`${API_BASE}/commands/${stage}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json()
    // Handle various error formats from FastAPI
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || error.detail?.msg || JSON.stringify(error.detail) || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function getPipelineTaskStatus(taskId: string): Promise<PipelineTaskStatusResponse> {
  return fetchApi<PipelineTaskStatusResponse>(`/commands/task/${taskId}`)
}

export async function getEpisodeTasks(episodeId: string): Promise<EpisodeTasksResponse> {
  return fetchApi<EpisodeTasksResponse>(`/commands/episode/${episodeId}/tasks`)
}

// Episode Browser API (cross-podcast)
export async function getAllEpisodes(
  limit: number = 20,
  offset: number = 0,
  filters?: EpisodeFilters
): Promise<AllEpisodesResponse> {
  const params = new URLSearchParams()
  params.set('limit', limit.toString())
  params.set('offset', offset.toString())

  if (filters) {
    if (filters.search) params.set('search', filters.search)
    if (filters.podcast_slug) params.set('podcast_slug', filters.podcast_slug)
    if (filters.state) params.set('state', filters.state)
    if (filters.date_from) params.set('date_from', filters.date_from)
    if (filters.date_to) params.set('date_to', filters.date_to)
    if (filters.sort_by) params.set('sort_by', filters.sort_by)
    if (filters.sort_order) params.set('sort_order', filters.sort_order)
  }

  return fetchApi<AllEpisodesResponse>(`/episodes?${params.toString()}`)
}

export async function bulkProcessEpisodes(episodeIds: string[]): Promise<BulkProcessResponse> {
  const response = await fetch(`${API_BASE}/episodes/bulk/process`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ episode_ids: episodeIds }),
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || error.detail?.msg || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// ============================================================================
// Dead Letter Queue (DLQ) API
// ============================================================================

export async function getDLQTasks(
  limit: number = 100,
  branch: DLQBranchFilter = 'all',
): Promise<DLQListResponse> {
  return fetchApi<DLQListResponse>(`/commands/dlq?limit=${limit}&branch=${branch}`)
}

export async function retryDLQTask(taskId: string): Promise<DLQActionResponse> {
  const response = await fetch(`${API_BASE}/commands/dlq/${taskId}/retry`, {
    method: 'POST',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function skipDLQTask(taskId: string): Promise<DLQActionResponse> {
  const response = await fetch(`${API_BASE}/commands/dlq/${taskId}/skip`, {
    method: 'POST',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function retryAllDLQTasks(taskIds?: string[]): Promise<DLQBulkRetryResponse> {
  const response = await fetch(`${API_BASE}/commands/dlq/retry-all`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: taskIds ? JSON.stringify({ task_ids: taskIds }) : '{}',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// ============================================================================
// Queue Viewer API
// ============================================================================

export async function getQueueTasks(completedLimit: number = 10): Promise<QueueTasksResponse> {
  return fetchApi<QueueTasksResponse>(`/commands/queue/tasks?completed_limit=${completedLimit}`)
}

export async function bumpQueueTask(taskId: string): Promise<BumpTaskResponse> {
  const response = await fetch(`${API_BASE}/commands/queue/task/${taskId}/bump`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function cancelQueueTask(taskId: string): Promise<CancelTaskResponse> {
  const response = await fetch(`${API_BASE}/commands/queue/task/${taskId}/cancel`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// ============================================================================
// Episode Failure API
// ============================================================================

export async function getFailedEpisodes(limit: number = 100): Promise<FailedEpisodesResponse> {
  return fetchApi<FailedEpisodesResponse>(`/episodes/failed?limit=${limit}`)
}

export async function getEpisodeFailure(episodeId: string): Promise<EpisodeFailure> {
  return fetchApi<EpisodeFailure>(`/episodes/${episodeId}/failure`)
}

export async function retryFailedEpisode(episodeId: string): Promise<EpisodeRetryResponse> {
  const response = await fetch(`${API_BASE}/episodes/${episodeId}/retry`, {
    method: 'POST',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// ============================================================================
// Full Pipeline API
// ============================================================================

export async function runPipeline(request: RunPipelineRequest): Promise<RunPipelineResponse> {
  const response = await fetch(`${API_BASE}/commands/run-pipeline`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function cancelPipeline(episodeId: string): Promise<CancelPipelineResponse> {
  const response = await fetch(`${API_BASE}/commands/episode/${episodeId}/cancel-pipeline`, {
    method: 'POST',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

export async function getEpisodeTasksExtended(episodeId: string): Promise<ExtendedEpisodeTasksResponse> {
  return fetchApi<ExtendedEpisodeTasksResponse>(`/commands/episode/${episodeId}/tasks`)
}

// ============================================================================
// Narration API (spec #33, keyed by briefing)
// ============================================================================

export async function narrateBriefing(
  briefingId: string,
  request: NarrateBriefingRequest = {},
): Promise<NarrateBriefingResponse> {
  const response = await fetch(`${API_BASE}/briefings/${briefingId}/narrate`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message =
      typeof error.detail === 'string'
        ? error.detail
        : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }
  return response.json()
}

export async function getNarration(narrationId: string): Promise<NarrationDetail> {
  return fetchApi<NarrationDetail>(`/narrations/${encodeURIComponent(narrationId)}`)
}

// ============================================================================
// Search API (spec #28 §4)
// ============================================================================

// Quick typeahead — pinned to lexical mode server-side. Pass an
// AbortSignal so each keystroke can cancel the prior in-flight call.
export async function quickSearch(
  q: string,
  opts: QuickSearchOptions = {},
  signal?: AbortSignal,
): Promise<QuickSearchResponse> {
  const params = new URLSearchParams()
  params.set('q', q)
  if (opts.limit_per_group !== undefined) params.set('limit_per_group', String(opts.limit_per_group))
  if (opts.podcast_id) params.set('podcast_id', opts.podcast_id)
  if (opts.podcast_slug) params.set('podcast_slug', opts.podcast_slug)
  if (opts.date_from) params.set('date_from', opts.date_from)
  if (opts.date_to) params.set('date_to', opts.date_to)
  if (opts.has_entity) {
    for (const id of opts.has_entity) params.append('has_entity', id)
  }
  const response = await fetch(`${API_BASE}/search/quick?${params.toString()}`, {
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Search error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

// Full corpus search — used by the /search results page (Phase 4.2).
// Defaults to hybrid mode (the LLM-friendly default); the ⌘K bar uses
// quickSearch above instead.
export async function corpusSearch(
  q: string,
  opts: CorpusSearchOptions = {},
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const params = new URLSearchParams()
  params.set('q', q)
  if (opts.mode) params.set('mode', opts.mode)
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.podcast_id) params.set('podcast_id', opts.podcast_id)
  if (opts.date_from) params.set('date_from', opts.date_from)
  if (opts.date_to) params.set('date_to', opts.date_to)
  if (opts.has_entity) {
    for (const id of opts.has_entity) params.append('has_entity', id)
  }
  const response = await fetch(`${API_BASE}/search/corpus?${params.toString()}`, {
    credentials: 'include',
    signal,
  })
  if (!response.ok) {
    throw new Error(`Search error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}


// Spec #28 §5.2 — episode-page entity UX. Two endpoints, both wrap
// existing repository methods on the backend.

export async function getEpisodeEntities(
  episodeId: string,
  minConfidence = 0,
): Promise<EpisodeEntitiesResponse> {
  const params = new URLSearchParams()
  if (minConfidence > 0) params.set('min_confidence', String(minConfidence))
  const qs = params.toString()
  return fetchApi<EpisodeEntitiesResponse>(
    `/episodes/${episodeId}/entities${qs ? `?${qs}` : ''}`,
  )
}

// Spec #28 §5.2 — "Related episodes" rail. Embedding-only on the
// backend (centroid of the episode's chunk vectors); returns the
// nearest distinct episodes with the source excluded.
export async function getRelatedEpisodes(
  episodeId: string,
  limit = 5,
): Promise<RelatedEpisodesResponse> {
  const params = new URLSearchParams({ episode_id: episodeId, limit: String(limit) })
  return fetchApi<RelatedEpisodesResponse>(`/search/related?${params.toString()}`)
}

export async function getEntitySummary(
  entityType: EntityType,
  idSlug: string,
): Promise<EntitySummaryResponse> {
  return fetchApi<EntitySummaryResponse>(`/entities/${entityType}/${idSlug}`)
}

// ============================================================================
// Inbox API
// ============================================================================
// Endpoints correspond to ``InboxService`` on the backend. Routes are added
// in a follow-up; the client surface is defined now so the frontend
// integration can move in parallel.

export interface GetInboxOptions {
  state?: InboxState
  limit?: number
  before?: string  // ISO-8601 cursor — return rows older than this delivered_at
}

export async function getInbox(
  options: GetInboxOptions = {},
): Promise<InboxListResponse> {
  const params = new URLSearchParams()
  if (options.state) params.set('state', options.state)
  if (options.limit !== undefined) params.set('limit', String(options.limit))
  if (options.before) params.set('before', options.before)
  const qs = params.toString()
  return fetchApi<InboxListResponse>(`/inbox${qs ? `?${qs}` : ''}`)
}

export async function getInboxUnreadCount(): Promise<InboxUnreadCountResponse> {
  return fetchApi<InboxUnreadCountResponse>('/inbox/unread-count')
}

// View-driven read tracking: only ever transitions unread → read, and a
// missing inbox row is a quiet no-op server-side — safe to fire for any
// episode view without checking whether the episode was ever delivered.
export async function markInboxRead(
  episodeId: string,
): Promise<InboxMarkReadResponse> {
  const response = await fetch(`${API_BASE}/inbox/${episodeId}/read`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

export async function setInboxState(
  episodeId: string,
  state: InboxState,
): Promise<InboxStateResponse> {
  const response = await fetch(`${API_BASE}/inbox/${episodeId}/state`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ state }),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// Per-user briefings (spec #36). ``getLatestBriefing`` lazy-generates a
// fresh briefing if the throttle has elapsed and inbox items are eligible.
// Spec #55 adds a 202 pending response and ``force=true`` escape hatch;
// callers should still treat 404 as "no briefing for now, hide the card".
export async function getLatestBriefing(force = false): Promise<LatestBriefingResponse> {
  if (!force) return fetchApi<LatestBriefingResponse>('/briefings/latest')

  const response = await fetch(`${API_BASE}/briefings/latest?force=true`, {
    credentials: 'include',
  })
  if (response.status === 404) {
    throw new Error('No episodes are ready yet. Your briefing is still catching up.')
  }
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json()
}

export async function getBriefings(
  limit: number = 20,
  offset: number = 0,
): Promise<BriefingsListResponse> {
  const params = new URLSearchParams()
  params.set('limit', limit.toString())
  params.set('offset', offset.toString())
  return fetchApi<BriefingsListResponse>(`/briefings?${params.toString()}`)
}

export async function getBriefing(briefingId: string): Promise<BriefingResponse> {
  return fetchApi<BriefingResponse>(`/briefings/${briefingId}`)
}

export async function getBriefingScript(briefingId: string): Promise<BriefingScriptResponse> {
  return fetchApi<BriefingScriptResponse>(`/briefings/${briefingId}/script`)
}

export async function markBriefingListened(briefingId: string): Promise<BriefingResponse> {
  const response = await fetch(`${API_BASE}/briefings/${briefingId}/listened`, {
    method: 'POST',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// Briefing schedule (spec #50). ``getBriefingSchedule`` 404s when the user
// has never configured one — callers should treat that as "scheduling off,
// show defaults".
export async function getBriefingSchedule(): Promise<BriefingScheduleResponse> {
  return fetchApi<BriefingScheduleResponse>('/briefings/schedule')
}

export async function putBriefingSchedule(
  update: BriefingScheduleUpdate,
): Promise<BriefingScheduleResponse> {
  const response = await fetch(`${API_BASE}/briefings/schedule`, {
    method: 'PUT',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(update),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}

// ============================================================================
// Imports (spec #31) — POST /api/imports
// ============================================================================

export async function importEpisode(request: ImportRequest): Promise<ImportResponse> {
  const response = await fetch(`${API_BASE}/imports`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }

  return response.json()
}
