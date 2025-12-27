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
} from './types'

const API_BASE = '/api'

async function fetchApi<T>(endpoint: string): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`)
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
