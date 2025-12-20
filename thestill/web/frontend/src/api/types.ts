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
  podcast_title: string
  podcast_id: string
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
  episode_index: number
  title: string
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
