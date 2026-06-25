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

export interface NarrationLatestSummary {
  narration_id: string
  digest_id: string | null
  generated_at: string | null
  mode: 'narrated' | 'fallback' | null
  fallback_reason: string | null
  target_duration_seconds: number | null
  actual_duration_seconds: number | null
  latency_ms: number | null
}

export interface NarrationDashboardStats {
  status: string
  timestamp: string
  total_runs: number
  fallback_count: number
  fallback_rate: number
  avg_actual_duration_seconds: number | null
  avg_target_duration_seconds: number | null
  avg_latency_ms: number | null
  latest: NarrationLatestSummary | null
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
  // THES-146: New metadata fields
  author?: string | null
  explicit?: boolean | null
  show_type?: string | null  // 'episodic' or 'serial'
  website_url?: string | null
  is_complete?: boolean
  copyright?: string | null
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
  // Category metadata (Apple Podcasts taxonomy)
  primary_category: string | null
  primary_subcategory: string | null
  secondary_category: string | null
  secondary_subcategory: string | null
  last_processed: string | null
  episodes_count: number
  episodes_processed: number
  is_following: boolean
  // THES-146: New metadata fields
  author?: string | null
  explicit?: boolean | null
  show_type?: string | null  // 'episodic' or 'serial'
  website_url?: string | null
  is_complete?: boolean
  copyright?: string | null
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
  description: string  // Plain text description (for CLI, LLM prompts)
  description_html?: string  // HTML description with links (for web UI)
  pub_date: string | null
  audio_url: string
  duration: number | null  // Duration in seconds
  duration_formatted: string | null  // Human-readable duration (e.g., '1:08:01')
  external_id: string
  state: 'discovered' | 'downloaded' | 'downsampled' | 'transcribed' | 'cleaned' | 'summarized'
  transcript_available: boolean
  summary_available: boolean
  image_url: string | null  // Episode-specific artwork
  summary_preview: string | null  // Preview text from summary (The Gist section)
  // THES-146: New metadata fields
  explicit?: boolean | null
  episode_type?: string | null  // 'full', 'trailer', or 'bonus'
  episode_number?: number | null
  season_number?: number | null
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
  description: string  // Plain text description (for CLI, LLM prompts)
  description_html?: string  // HTML description with links (for web UI)
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
  // THES-146: New metadata fields
  explicit?: boolean | null
  episode_type?: string | null  // 'full', 'trailer', or 'bonus'
  episode_number?: number | null
  season_number?: number | null
  website_url?: string | null
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

// Segmented-cleanup types (spec #18 Phase D). Mirror the Pydantic
// models in thestill/models/annotated_transcript.py. Keep the field
// list in lock-step with the Python side — they are the contract the
// SegmentedTranscriptViewer renders from.
export type SegmentKind = 'content' | 'filler' | 'ad_break' | 'music' | 'intro' | 'outro'

export interface WordSpan {
  start_segment_id: number
  start_word_index: number
  end_segment_id: number
  end_word_index: number
}

export interface AnnotatedSegment {
  id: number
  start: number
  end: number
  speaker: string | null
  text: string
  kind: SegmentKind
  sponsor: string | null
  source_segment_ids: number[]
  source_word_span: WordSpan | null
  user_segment_id: string | null
  metadata: Record<string, unknown>
}

export interface AnnotatedTranscriptDump {
  episode_id: string
  segments: AnnotatedSegment[]
  playback_time_offset_seconds: number
  algorithm_version: string
  // Duration of the audio file that was transcribed. Compared against
  // the live audio element's duration to detect DAI-induced drift.
  // `null` for legacy transcripts cleaned before we recorded this.
  transcript_source_duration_s: number | null
}

// ---------------------------------------------------------------------------
// Spec #38 — karaoke wipe word-level timestamps
// ---------------------------------------------------------------------------
//
// Short field names mirror the backend Pydantic DTOs (``WordTimestamp``) —
// chosen to keep a 10k-word episode's payload around 100–150 KB gzipped.
// The hook layer transforms ``segments`` into ``Map<segment_id, WordTimestamp[]>``
// so the viewer doesn't repeat the work per render.

export interface WordTimestamp {
  w: string
  s: number
  e: number
}

export interface SegmentWords {
  segment_id: number
  words: WordTimestamp[]
}

export interface TranscriptWordsResponse {
  status: string
  timestamp: string
  episode_id: string
  playback_time_offset_seconds: number
  segments: SegmentWords[]
}

// Pre-indexed view the karaoke driver consumes. ``offset`` is the
// ``playback_time_offset_seconds`` to add to each word's ``s``/``e`` before
// comparing to the audio element's ``currentTime``.
export interface KaraokeWordsByEpisode {
  episodeId: string
  offset: number
  wordsBySegmentId: Map<number, WordTimestamp[]>
}

export interface ContentResponse {
  status: string
  timestamp: string
  episode_id: string
  episode_title: string
  content: string
  available: boolean
  transcript_type?: TranscriptType  // 'cleaned' or 'raw', undefined if not available
  // Present iff the segmented-cleanup JSON sidecar exists for this
  // episode. Absence means "no segmented output to render" — the
  // frontend falls back to the classic ``content`` Markdown.
  segments?: AnnotatedTranscriptDump
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

// Top Podcasts API Types
export interface TopPodcast {
  rank: number
  name: string
  artist: string | null
  rss_url: string
  apple_url: string | null
  youtube_url: string | null
  category: string | null
  source_genre: string | null
  is_following: boolean
  // Slug of the local ``podcasts`` row when this chart entry has already
  // been imported. ``null`` means the podcast must be lazy-imported via
  // POST /api/podcasts/resolve before the detail page can render.
  podcast_slug: string | null
  // Artwork URL surfaced from the local ``podcasts`` row (stored from the
  // RSS feed at import time). ``null`` for chart-only entries; the UI
  // falls back to a placeholder.
  image_url: string | null
}

// Resolve API Types (lazy-import path used by Top Podcasts)
export interface ResolvePodcastRequest {
  url: string
}

export interface ResolvePodcastResponse {
  status: string
  timestamp: string
  podcast_slug: string
  podcast_id: string
  is_new: boolean
}

export interface TopPodcastsResponse {
  status: string
  timestamp: string
  region: string
  available_regions: string[]
  // Distinct category names present in the resolved region's chart. Not
  // affected by ``q`` or ``category`` filters — drives the category picker
  // without an extra round-trip.
  available_categories: string[]
  user_region: string | null
  count: number
  top_podcasts: TopPodcast[]
}

// Pipeline Task Types (Queue-based)
// Full mirror of the backend ``TaskStage`` enum (thestill/core/queue_manager.py):
// the user chain (download → summarize), the spec #28/#46/#47 entity branch
// (extract-entities → … → enrich-entities), and the spec #48 podcast-scoped
// producer (refresh-feed).
export type PipelineStage =
  | 'download'
  | 'downsample'
  | 'transcribe'
  | 'clean'
  | 'summarize'
  | 'extract-entities'
  | 'resolve-entities'
  | 'reindex'
  | 'rebuild-cooccurrences'
  | 'compute-related'
  | 'enrich-entities'
  | 'refresh-feed'
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
  // Feed-scoped (REFRESH_FEED) tasks are podcast-scoped: episode_id is null
  // and podcast_id is set. Episode-scoped tasks are the inverse.
  episode_id: string | null
  podcast_id: string | null
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

// Spec #28 Phase 3.2 — DLQ filter to keep entity-branch failures from
// drowning the user-facing critical path in the queue viewer.
export type DLQBranchFilter = 'all' | 'user' | 'entity' | 'feed'

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
// Queue Viewer Types
// ============================================================================

export interface QueuedTaskWithContext {
  task_id: string
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_title: string
  podcast_slug: string
  stage: PipelineStage
  status: string // 'pending' | 'processing' | 'completed' | 'retry_scheduled'
  priority: number
  // Time tracking
  created_at: string | null
  started_at: string | null
  completed_at: string | null
  time_in_queue_seconds: number | null
  processing_time_seconds: number | null
  wait_time_seconds: number | null // Time from created to started (for completed)
  // Episode metadata
  duration_seconds: number | null
  duration_formatted: string | null
  // Retry info
  retry_count: number
  next_retry_at: string | null
}

export interface StageWorkerStatus {
  stage: PipelineStage
  active: number // Currently processing
  capacity: number // Max parallel jobs for this stage
  pending: number // Tasks waiting for this stage
  retry_scheduled: number // Tasks in backoff for this stage
}

export interface QueueTasksResponse {
  status: string
  timestamp: string
  worker_running: boolean
  // Per-stage worker pools (pipeline order)
  stages: StageWorkerStatus[]
  processing_tasks: QueuedTaskWithContext[]
  pending_tasks: QueuedTaskWithContext[]
  retry_scheduled_tasks: QueuedTaskWithContext[]
  completed_tasks: QueuedTaskWithContext[]
  pending_count: number
  processing_count: number
  retry_scheduled_count: number
  completed_shown: number
}

export interface BumpTaskResponse {
  status: string
  task_id: string
  message: string
}

export interface CancelTaskResponse {
  status: string
  task_id: string
  message: string
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

// ============================================================================
// Digest Types
// ============================================================================

export type DigestStatus = 'pending' | 'in_progress' | 'completed' | 'partial' | 'failed'

export interface Digest {
  id: string
  user_id: string
  created_at: string
  updated_at: string
  period_start: string
  period_end: string
  status: DigestStatus
  file_path: string | null
  episode_ids: string[]
  episodes_total: number
  episodes_completed: number
  episodes_failed: number
  processing_time_seconds: number | null
  error_message: string | null
  success_rate: number
  is_complete: boolean
}

export interface DigestsResponse {
  status: string
  timestamp: string
  digests: Digest[]
  count: number
  total: number
  offset: number
  limit: number
  has_more: boolean
  next_offset: number | null
}

export type NarrationMode = 'narrated' | 'fallback'

export interface NarrationSummary {
  narration_id: string
  slug: string
  target_duration_seconds: number | null
  actual_duration_seconds: number | null
  mode: NarrationMode | null
  fallback_reason: string | null
  generated_at: string | null
  schema_version: string | null
  script_path: string
  markdown_path: string | null
}

export interface NarrateDigestRequest {
  target_duration?: number | string
  slug?: string
}

export interface NarrateDigestResponse {
  status: string
  timestamp: string
  narration_id: string
  digest_id: string
  slug: string
  mode: NarrationMode
  target_duration_seconds: number
  actual_duration_seconds: number
  quote_count: number
  fallback_reason: string | null
  script_path: string | null
  markdown_path: string | null
}

export interface NarrationDetail {
  status: string
  timestamp: string
  id: string
  script: Record<string, unknown>
  markdown: string | null
}

export interface DigestDetailResponse {
  status: string
  timestamp: string
  digest: Digest
  narrations: NarrationSummary[]
}

export interface DigestContentResponse {
  status: string
  timestamp: string
  digest_id: string
  content: string | null
  available: boolean
  error?: string
}

export interface DigestEpisodeInfo {
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_id: string
  podcast_title: string
  podcast_slug: string
  state: string
  pub_date: string | null
  duration: number | null
  image_url: string | null
}

export interface DigestEpisodesResponse {
  status: string
  timestamp: string
  digest_id: string
  episodes: DigestEpisodeInfo[]
  count: number
}

export interface CreateDigestRequest {
  since_days?: number
  max_episodes?: number
  podcast_id?: string
  ready_only?: boolean
  exclude_digested?: boolean
}

export interface CreateDigestResponse {
  status: string
  timestamp: string
  message: string
  digest_id: string | null
  episodes_selected: number
}

export interface DigestPreviewEpisode {
  episode_id: string
  episode_title: string
  episode_slug: string
  podcast_id: string
  podcast_title: string
  podcast_slug: string
  state: string
  pub_date: string | null
}

// ============================================================================
// Spec #28 §4 — Search types (corpus + quick typeahead)
// ============================================================================

export type SearchMode = 'lexical' | 'semantic' | 'hybrid'

export interface SearchResult {
  episode_id: string
  podcast_id: string
  // Slugs are populated by the API layer (api_search.py); they may be
  // null for legacy episode rows that pre-date the slug migration. The
  // wire-format `web_url`/`deeplink` carry the legacy `/episodes/<id>`
  // shape for MCP/desktop callers — the web client uses slugs instead.
  podcast_slug: string | null
  episode_slug: string | null
  podcast_title: string
  episode_title: string
  published_at: string | null
  start_ms: number
  end_ms: number
  speaker: string | null
  quote: string
  score: number
  match_type: string
  deeplink: string
  web_url: string
  // Spec #28 §4.2 — populated so the search results page can play the
  // matching quote inline through the FloatingPlayer instead of
  // navigating to the episode page.
  audio_url?: string | null
  image_url?: string | null
  duration?: number | null
}

export interface SearchResponse {
  query: string
  mode: SearchMode
  total: number
  results: SearchResult[]
}

export interface CorpusSearchOptions {
  mode?: SearchMode
  limit?: number
  podcast_id?: string
  date_from?: string
  date_to?: string
  has_entity?: string[]
}

// Spec #28 §5.2 — "Related episodes" rail. The backend averages the
// source episode's chunk embeddings into a centroid and returns the
// nearest distinct episodes (source excluded). Slugs are always
// present (rows without them are dropped server-side), so cards are
// always deep-linkable.
export interface RelatedEpisode {
  episode_id: string
  podcast_id: string
  podcast_slug: string
  episode_slug: string
  podcast_title: string
  episode_title: string
  published_at: string | null
  image_url: string | null
  score: number
}

export interface RelatedEpisodesResponse {
  episode_id: string
  episodes: RelatedEpisode[]
}

// Quick search (⌘K typeahead) — pinned to lexical server-side, never
// silently upgraded. Items are discriminated by `kind` so the
// CommandBar can render mixed groups uniformly.

export interface QuickEpisodeItem {
  kind: 'episode'
  episode_id: string
  podcast_id: string
  podcast_slug: string
  episode_slug: string
  title: string
  podcast_title: string
  pub_date: string | null
  image_url: string | null
}

export interface QuickEntityItem {
  kind: 'entity'
  entity_type: 'person' | 'company' | 'product' | 'topic'
  id: string
  name: string
  matched_alias: string | null
  mention_count: number
  role: 'guest' | 'host' | 'recurring' | null
  role_episode_count: number
}

export interface QuickQuoteItem {
  kind: 'quote'
  episode_id: string
  podcast_id: string
  podcast_slug: string
  episode_slug: string
  podcast_title: string
  episode_title: string
  speaker: string | null
  quote: string
  start_ms: number
  end_ms: number
  score: number
  // Spec #28 §4.1 — populated so the ⌘K command bar can seek the
  // FloatingPlayer inline when a quote row is selected, instead of
  // closing the bar and navigating to the episode page.
  audio_url?: string | null
  image_url?: string | null
  duration?: number | null
}

export type QuickSearchItem = QuickEpisodeItem | QuickEntityItem | QuickQuoteItem

export type QuickGroupType = 'episode' | 'person' | 'company' | 'topic' | 'quote'

export interface QuickGroup {
  type: QuickGroupType
  label: string
  items: QuickSearchItem[]
}

export interface QuickSearchResponse {
  query: string
  took_ms: number
  groups: QuickGroup[]
  see_all_url: string
}

export interface QuickSearchOptions {
  limit_per_group?: number
  podcast_id?: string
  podcast_slug?: string
  date_from?: string
  date_to?: string
  has_entity?: string[]
}

export interface DigestPreviewResponse {
  status: string
  timestamp: string
  episodes: DigestPreviewEpisode[]
  total_matching: number
  criteria: {
    since_days: number
    max_episodes: number
    podcast_id: string | null
    ready_only: boolean
    exclude_digested: boolean
  }
}

// ---------------------------------------------------------------------------
// Spec #28 §5.2 — episode-page entity UX
// ---------------------------------------------------------------------------

export type EntityType = 'person' | 'company' | 'product' | 'topic'
export type SpeakerKind = 'host' | 'guest' | 'recurring' | 'unknown'

export interface EntityRef {
  id: string
  type: EntityType
  canonical_name: string
  wikidata_qid: string | null
}

export interface MentionLite {
  id: number
  entity_id: string
  segment_id: number
  start_ms: number
  end_ms: number
  speaker: string | null
  role: string | null
  surface_form: string
  quote_excerpt: string
  confidence: number
  sentiment: number | null
}

export interface EpisodeEntity {
  entity: EntityRef
  mention_count: number
  first_mention_ms: number
  speaker_kind: SpeakerKind
  // Spec #28 §5.2 — composite relevance score; see backend
  // ``_compute_salience``. Used to sort the rail; the frontend can
  // also use it to filter very-low-salience entries.
  salience: number
  mentions: MentionLite[]
}

export interface EpisodeEntitiesResponse {
  episode_id: string
  podcast_id: string
  entities: EpisodeEntity[]
}

export interface EntityCooccurrenceRef {
  entity: EntityRef
  episode_count: number
  last_seen_at: string | null
}

export interface EntityCitationRow {
  episode_id: string
  podcast_id: string
  podcast_slug: string | null
  episode_slug: string | null
  podcast_title: string
  episode_title: string
  published_at: string | null
  start_ms: number
  end_ms: number
  speaker: string | null
  quote: string
  surface_form: string
  // Spec #28 §5.1 — present so the entity page can hand them straight
  // to the FloatingPlayer without a second round-trip. Optional because
  // older API responses (or episodes whose audio_url was never set)
  // shouldn't blank the row.
  audio_url?: string | null
  image_url?: string | null
  duration?: number | null
}

export interface HostedPodcastRef {
  podcast_id: string
  podcast_slug: string | null
  podcast_title: string
  episode_count: number
}

export interface GuestEpisodeRef {
  episode_id: string
  episode_slug: string | null
  episode_title: string
  podcast_id: string
  podcast_slug: string | null
  podcast_title: string
  published_at: string | null
}

// Spec #45 Tier 0 — Wikidata/Wikipedia enrichment surfaced on the entity page.
export interface EntityFact {
  label: string
  value: string
  url?: string | null
}

export interface EntityAffiliation {
  qid?: string | null
  label: string
  relation: string
  entity_id?: string | null
  entity_type?: EntityType | null
}

export interface EntityEnrichment {
  image_url?: string | null
  image_attribution?: string | null
  image_license?: string | null
  headline?: string | null
  wikipedia_extract?: string | null
  wikipedia_url?: string | null
  facts: EntityFact[]
  affiliations: EntityAffiliation[]
}

export interface MostDiscussedRef {
  podcast_id: string
  podcast_slug: string | null
  podcast_title: string
  mention_count: number
}

export interface EntitySummaryResponse {
  entity: EntityRef
  aliases: string[]
  description: string | null
  mention_count: number
  cooccurring: EntityCooccurrenceRef[]
  recent_mentions: EntityCitationRow[]
  hosts_podcasts: HostedPodcastRef[]
  recurring_podcasts: HostedPodcastRef[]
  guest_episodes: GuestEpisodeRef[]
  // Spec #45 — optional; older responses / un-enriched entities omit these.
  most_discussed_on?: MostDiscussedRef[]
  enrichment?: EntityEnrichment | null
}

// ============================================================================
// Inbox API
// ============================================================================
//
// Two write paths fill the inbox:
//   - follow_new:  an episode the user follows just published.
//   - follow_seed: a few recent episodes pulled in when the user follows.
// State is per-user; two users following the same podcast keep independent
// state on the same episode.

export type InboxSource = 'follow_new' | 'follow_seed' | 'ad_hoc' | 'import'
export type InboxState = 'unread' | 'read' | 'saved' | 'dismissed'

export interface InboxEntry {
  id: string
  user_id: string
  episode_id: string
  source: InboxSource
  state: InboxState
  delivered_at: string  // ISO-8601
  state_changed_at: string | null
}

// Minimal podcast subset rendered alongside each inbox row.
export interface InboxPodcastSummary {
  id: string
  title: string
  slug: string
  image_url: string | null
}

// Composed read view: an inbox row + the episode + minimal podcast info.
export interface InboxItem {
  entry: InboxEntry
  episode: Episode
  podcast: InboxPodcastSummary
}

export interface InboxListResponse {
  status: string
  timestamp: string
  items: InboxItem[]
  count: number
  // Cursor for the next page — pass back as ``before=`` to fetch older rows.
  next_before: string | null
}

export interface InboxUnreadCountResponse {
  status: string
  timestamp: string
  unread_count: number
}

export interface InboxStateRequest {
  state: InboxState
}

export interface InboxStateResponse {
  status: string
  timestamp: string
  entry: InboxEntry
}

// Per-user briefings (spec #36)
// ============================================================================
//
// A briefing is a recurring readout of the inbox subset the user hasn't
// acted on yet. The window covered is ``[cursor_from, cursor_to)``;
// ``script_path`` and ``audio_path`` are NULL when rendering hasn't run
// yet (rare in practice — production wires a renderer in).

export interface Briefing {
  id: string
  user_id: string
  cursor_from: string  // ISO-8601
  cursor_to: string    // ISO-8601
  episode_count: number
  script_path: string | null
  audio_path: string | null
  created_at: string
  listened_at: string | null
}

export interface BriefingResponse extends Briefing {
  status: string
  timestamp: string
}

export interface BriefingScriptResponse {
  status: string
  timestamp: string
  markdown: string
}

// ============================================================================
// Imports (spec #31) — POST /api/imports
// ============================================================================

export type ImportKind = 'bare_audio' | 'youtube' | 'rss_episode'

export interface ImportRequest {
  url: string
}

// Parent podcast surfaced for the post-import "Follow this channel" CTA.
// Null when the import fell back to the synthetic audio-imports row.
export interface ImportParent {
  id: string
  title: string
  slug: string
}

export interface ImportPayload {
  episode_id: string
  canonical_id: string
  title: string
  kind: ImportKind
  source_handle: string
  deduplicated: boolean
  inbox_created: boolean
  inbox_entry: InboxEntry
  parent: ImportParent | null
}

export interface ImportResponse {
  status: string
  timestamp: string
  import: ImportPayload
}
