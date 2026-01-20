// API Response Types

// Failure type (used in Episode and other types)
export type FailureType = 'transient' | 'fatal'

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
  duration: number | null  // Duration in seconds
  duration_formatted: string | null  // Human-readable duration (e.g., '1:08:01')
  episode_image_url: string | null  // Episode-specific artwork
  podcast_image_url: string | null  // Podcast artwork (fallback)
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
  description: string  // Original description (may contain HTML)
  description_text: string  // Plain text version for display
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
  duration: number | null  // Duration in seconds
  duration_formatted: string | null  // Human-readable duration (e.g., '1:08:01')
  external_id: string
  state: 'discovered' | 'downloaded' | 'downsampled' | 'transcribed' | 'cleaned' | 'summarized'
  transcript_available: boolean
  summary_available: boolean
  image_url: string | null  // Episode-specific artwork
  // Failure info (optional - only present when episode has failed)
  is_failed?: boolean
  failed_at_stage?: string | null
  failure_reason?: string | null
  failure_type?: FailureType | null
  failed_at?: string | null
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
  duration: number | null  // Duration in seconds
  duration_formatted: string | null  // Human-readable duration (e.g., '1:08:01')
  external_id: string
  state: string
  has_transcript: boolean
  has_summary: boolean
  image_url: string | null  // Episode-specific artwork
  podcast_image_url: string | null  // Fallback: podcast artwork
  // Failure info
  is_failed?: boolean
  failed_at_stage?: string | null
  failure_reason?: string | null
  failure_type?: FailureType | null
  failed_at?: string | null
}

export interface EpisodeDetailResponse {
  status: string
  timestamp: string
  episode: EpisodeDetail
}

export type TranscriptType = 'cleaned' | 'raw'

export interface ContentResponse {
  status: string
  timestamp: string
  episode_id: string
  episode_title: string
  content: string
  available: boolean
  transcript_type?: TranscriptType  // 'cleaned' or 'raw', undefined if not available
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

// Pipeline Task Types (Queue-based)
export type PipelineStage = 'download' | 'downsample' | 'transcribe' | 'clean' | 'summarize'
export type PipelineTaskStatus = 'pending' | 'processing' | 'completed' | 'failed'

// Extended pipeline task status to include new states
export type ExtendedPipelineTaskStatus =
  | PipelineTaskStatus
  | 'retry_scheduled'
  | 'dead'

export interface PipelineTaskMetadata {
  run_full_pipeline?: boolean
  target_state?: string
  initiated_at?: string
  initiated_by?: string
}

export interface PipelineTaskRequest {
  podcast_slug: string
  episode_slug: string
}

export interface PipelineTaskResponse {
  task_id: string
  status: string
  message: string
  stage: PipelineStage
  episode_id: string
  episode_title: string
}

export interface PipelineTaskStatusResponse {
  task_id: string
  episode_id: string
  stage: PipelineStage
  status: PipelineTaskStatus
  error_message: string | null
  created_at: string | null
  updated_at: string | null
  started_at: string | null
  completed_at: string | null
}

export interface EpisodeTasksResponse {
  episode_id: string
  tasks: Array<{
    id: string
    episode_id: string
    stage: PipelineStage
    status: PipelineTaskStatus | ExtendedPipelineTaskStatus
    priority: number
    error_message: string | null
    created_at: string | null
    updated_at: string | null
    started_at: string | null
    completed_at: string | null
    retry_count: number
    max_retries: number
    next_retry_at: string | null
    error_type: 'transient' | 'fatal' | null
    last_error: string | null
    metadata: PipelineTaskMetadata | null
  }>
}

// Episode Browser Types
export type EpisodeState = 'discovered' | 'downloaded' | 'downsampled' | 'transcribed' | 'cleaned' | 'summarized'

export interface EpisodeWithPodcast extends Episode {
  podcast_title: string
  podcast_image_url: string | null  // Fallback: podcast artwork (use image_url first)
}

export interface AllEpisodesResponse {
  status: string
  timestamp: string
  episodes: EpisodeWithPodcast[]
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset: number | null
}

export interface EpisodeFilters {
  search?: string
  podcast_slug?: string
  state?: EpisodeState
  date_from?: string
  date_to?: string
  sort_by?: 'pub_date' | 'title' | 'updated_at'
  sort_order?: 'asc' | 'desc'
}

export interface BulkProcessRequest {
  episode_ids: string[]
}

export interface BulkProcessTaskInfo {
  episode_id: string
  task_id: string
  stage: PipelineStage
}

export interface BulkProcessResponse {
  status: string
  queued: number
  skipped: number
  tasks: BulkProcessTaskInfo[]
}

// ============================================================================
// Dead Letter Queue (DLQ) Types
// ============================================================================

export interface DLQTask {
  task_id: string
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_title: string
  podcast_slug: string
  stage: PipelineStage
  error_message: string | null
  error_type: 'transient' | 'fatal' | null
  retry_count: number
  max_retries: number
  created_at: string | null
  completed_at: string | null
}

export interface DLQListResponse {
  status: string
  tasks: DLQTask[]
  count: number
}

export interface DLQActionResponse {
  status: string
  message: string
  task_id: string
  new_status: string
}

export interface DLQBulkRetryRequest {
  task_ids?: string[]
}

export interface DLQBulkRetryResponse {
  status: string
  retried: number
  skipped: number
  task_ids: string[]
}

// ============================================================================
// Episode Failure Types
// ============================================================================

export interface EpisodeFailure {
  status: string
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_title: string
  podcast_slug: string
  is_failed: boolean
  failed_at_stage: string | null
  failure_reason: string | null
  failure_type: FailureType | null
  failed_at: string | null
  last_successful_state: EpisodeState
  can_retry: boolean
}

export interface FailedEpisodesResponse {
  status: string
  episodes: EpisodeFailure[]
  count: number
}

export interface EpisodeRetryResponse {
  status: string
  message: string
  episode_id: string
  task_id: string | null
  stage: string | null
}

// Extend Episode type to include failure info
export interface EpisodeWithFailure extends Episode {
  is_failed?: boolean
  failed_at_stage?: string | null
  failure_reason?: string | null
  failure_type?: FailureType | null
  failed_at?: string | null
}

// ============================================================================
// Full Pipeline Types
// ============================================================================

export interface ExtendedPipelineTask {
  id: string
  episode_id: string
  stage: PipelineStage
  status: ExtendedPipelineTaskStatus
  priority: number
  error_message: string | null
  error_type: 'transient' | 'fatal' | null
  retry_count: number
  max_retries: number
  next_retry_at: string | null
  last_error: string | null
  created_at: string | null
  updated_at: string | null
  started_at: string | null
  completed_at: string | null
  metadata: PipelineTaskMetadata | null
}

export interface RunPipelineRequest {
  podcast_slug: string
  episode_slug: string
  target_state?: string // defaults to 'summarized'
}

export interface RunPipelineResponse {
  task_id: string
  status: string
  message: string
  starting_stage: PipelineStage
  target_state: string
  episode_id: string
  episode_title: string
}

export interface CancelPipelineResponse {
  status: string
  message: string
  episode_id: string
  cancelled_tasks: number
}

// Extended episode tasks response with retry info
export interface ExtendedEpisodeTasksResponse {
  episode_id: string
  tasks: ExtendedPipelineTask[]
  pipeline_status?: {
    is_running: boolean
    target_state: string | null
    current_stage: PipelineStage | null
    completed_stages: PipelineStage[]
    pending_stages: PipelineStage[]
  }
}
