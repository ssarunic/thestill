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

export async function getPodcast(podcastId: string): Promise<PodcastDetailResponse> {
  return fetchApi<PodcastDetailResponse>(`/podcasts/${podcastId}`)
}

export async function getPodcastEpisodes(
  podcastId: string,
  limit = 20,
  offset = 0
): Promise<EpisodesResponse> {
  return fetchApi<EpisodesResponse>(`/podcasts/${podcastId}/episodes?limit=${limit}&offset=${offset}`)
}

// Episodes API
export async function getEpisode(episodeId: string): Promise<EpisodeDetailResponse> {
  return fetchApi<EpisodeDetailResponse>(`/episodes/${episodeId}`)
}

export async function getEpisodeTranscript(episodeId: string): Promise<ContentResponse> {
  return fetchApi<ContentResponse>(`/episodes/${episodeId}/transcript`)
}

export async function getEpisodeSummary(episodeId: string): Promise<ContentResponse> {
  return fetchApi<ContentResponse>(`/episodes/${episodeId}/summary`)
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
