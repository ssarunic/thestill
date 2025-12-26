// API Response Types

export interface DashboardStats {
  status: string
  timestamp: string
  podcasts_tracked: number
  episodes_total: number
  episodes_processed: number
  episodes_pending: number
  storage_path: string
  audio_files_count: number
  transcripts_available: number
  pipeline: {
    discovered: number
    downloaded: number
    downsampled: number
    transcribed: number
    cleaned: number
    summarized: number
  }
}

export interface ActivityItem {
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_title: string
  podcast_id: string
  podcast_slug: string
  action: string
  timestamp: string
  pub_date: string | null
}

export interface ActivityResponse {
  status: string
  timestamp: string
  items: ActivityItem[]
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset: number | null
}

export interface PodcastSummary {
  index: number
  title: string
  description: string
  rss_url: string
  slug: string
  image_url: string | null
  last_processed: string | null
  episodes_count: number
  episodes_processed: number
}

export interface PodcastsResponse {
  status: string
  timestamp: string
  podcasts: PodcastSummary[]
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset: number | null
}

export interface PodcastDetail {
  id: string
  index: number
  title: string
  description: string
  rss_url: string
  slug: string
  image_url: string | null
  last_processed: string | null
  episodes_count: number
  episodes_processed: number
}

export interface PodcastDetailResponse {
  status: string
  timestamp: string
  podcast: PodcastDetail
}

export interface Episode {
  id: string
  podcast_index: number
  podcast_slug: string
  episode_index: number
  title: string
  slug: string
  description: string
  pub_date: string | null
  audio_url: string
  duration: string | null
  external_id: string
  state: 'discovered' | 'downloaded' | 'downsampled' | 'transcribed' | 'cleaned' | 'summarized'
  transcript_available: boolean
  summary_available: boolean
}

export interface EpisodesResponse {
  status: string
  timestamp: string
  episodes: Episode[]
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset: number | null
}

export interface EpisodeDetail {
  id: string
  podcast_id: string
  podcast_slug: string
  podcast_title: string
  title: string
  description: string
  slug: string
  pub_date: string | null
  audio_url: string
  duration: string | null
  external_id: string
  state: string
  has_transcript: boolean
  has_summary: boolean
}

export interface EpisodeDetailResponse {
  status: string
  timestamp: string
  episode: EpisodeDetail
}

export interface ContentResponse {
  status: string
  timestamp: string
  episode_id: string
  episode_title: string
  content: string
  available: boolean
}

// Commands API Types
export interface RefreshRequest {
  podcast_id?: string
  max_episodes?: number
  dry_run?: boolean
}

export interface RefreshResponse {
  status: string
  message: string
  task_type: string
}

export interface RefreshTaskStatus {
  task_type: string
  status: 'none' | 'pending' | 'running' | 'completed' | 'failed'
  started_at: string | null
  completed_at: string | null
  progress: number
  message: string
  result: {
    total_episodes: number
    podcasts_refreshed: number
    dry_run: boolean
    episodes_by_podcast: Array<{
      podcast: string
      new_episodes: number
    }>
    podcast_filter?: string
  } | null
  error: string | null
}

export interface RefreshError {
  error: string
  started_at: string | null
  progress: number
  message: string
}

// Add Podcast API Types
export interface AddPodcastRequest {
  url: string
}

export interface AddPodcastResponse {
  status: string
  message: string
  task_type: string
}

export interface AddPodcastTaskStatus {
  task_type: string
  status: 'none' | 'pending' | 'running' | 'completed' | 'failed'
  started_at: string | null
  completed_at: string | null
  progress: number
  message: string
  result: {
    podcast_title: string
    podcast_id: string
    rss_url: string
    episodes_count: number
  } | null
  error: string | null
}
