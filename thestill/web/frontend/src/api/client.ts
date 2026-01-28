import type {
  DashboardStats,
  ActivityResponse,
  PodcastsResponse,
  PodcastDetailResponse,
  EpisodesResponse,
  EpisodeDetailResponse,
  ContentResponse,
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
  DigestsResponse,
  DigestDetailResponse,
  DigestContentResponse,
  DigestEpisodesResponse,
  CreateDigestRequest,
  CreateDigestResponse,
  DigestPreviewResponse,
  DigestStatus,
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

export async function getRecentActivity(limit = 10, offset = 0): Promise<ActivityResponse> {
  return fetchApi<ActivityResponse>(`/dashboard/activity?limit=${limit}&offset=${offset}`)
}

// Podcasts API
export async function getPodcasts(limit = 12, offset = 0): Promise<PodcastsResponse> {
  return fetchApi<PodcastsResponse>(`/podcasts?limit=${limit}&offset=${offset}`)
}

export async function getPodcast(podcastSlug: string): Promise<PodcastDetailResponse> {
  return fetchApi<PodcastDetailResponse>(`/podcasts/${podcastSlug}`)
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

export async function getEpisodeSummary(podcastSlug: string, episodeSlug: string): Promise<ContentResponse> {
  return fetchApi<ContentResponse>(`/podcasts/${podcastSlug}/episodes/${episodeSlug}/summary`)
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

export async function getDLQTasks(limit: number = 100): Promise<DLQListResponse> {
  return fetchApi<DLQListResponse>(`/commands/dlq?limit=${limit}`)
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
// Digest API
// ============================================================================

export async function getDigests(
  limit: number = 50,
  offset: number = 0,
  status?: DigestStatus
): Promise<DigestsResponse> {
  const params = new URLSearchParams()
  params.set('limit', limit.toString())
  params.set('offset', offset.toString())
  if (status) params.set('status', status)

  return fetchApi<DigestsResponse>(`/digests?${params.toString()}`)
}

export async function getDigest(digestId: string): Promise<DigestDetailResponse> {
  return fetchApi<DigestDetailResponse>(`/digests/${digestId}`)
}

export async function getLatestDigest(): Promise<DigestDetailResponse> {
  return fetchApi<DigestDetailResponse>('/digests/latest')
}

export async function getDigestContent(digestId: string): Promise<DigestContentResponse> {
  return fetchApi<DigestContentResponse>(`/digests/${digestId}/content`)
}

export async function getDigestEpisodes(digestId: string): Promise<DigestEpisodesResponse> {
  return fetchApi<DigestEpisodesResponse>(`/digests/${digestId}/episodes`)
}

export async function previewDigest(request: CreateDigestRequest): Promise<DigestPreviewResponse> {
  const response = await fetch(`${API_BASE}/digests/preview`, {
    method: 'POST',
    credentials: 'include',
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

export async function createDigest(request: CreateDigestRequest): Promise<CreateDigestResponse> {
  const response = await fetch(`${API_BASE}/digests`, {
    method: 'POST',
    credentials: 'include',
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

export async function deleteDigest(digestId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/digests/${digestId}`, {
    method: 'DELETE',
    credentials: 'include',
  })

  if (!response.ok) {
    const error = await response.json()
    const message = typeof error.detail === 'string'
      ? error.detail
      : error.detail?.error || `API error: ${response.status}`
    throw new Error(message)
  }
}

// ============================================================================
// Morning Briefing API (uses server-configured defaults)
// ============================================================================

export async function getMorningBriefing(): Promise<DigestPreviewResponse> {
  return fetchApi<DigestPreviewResponse>('/digests/morning-briefing')
}

export async function createMorningBriefing(): Promise<CreateDigestResponse> {
  const response = await fetch(`${API_BASE}/digests/morning-briefing`, {
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
