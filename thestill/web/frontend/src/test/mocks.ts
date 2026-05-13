import type { Briefing, BriefingStatus, BriefingPreviewEpisode, DashboardStats } from '../api/types'

// Factory functions for creating test data

export function createBriefing(overrides: Partial<Briefing> = {}): Briefing {
  return {
    id: 'briefing-123',
    user_id: 'user-123',
    created_at: '2026-01-26T10:00:00Z',
    updated_at: '2026-01-26T10:05:00Z',
    period_start: '2026-01-19T00:00:00Z',
    period_end: '2026-01-26T10:00:00Z',
    status: 'completed' as BriefingStatus,
    file_path: 'briefing_20260126_100000.md',
    episode_ids: ['ep-1', 'ep-2', 'ep-3'],
    episodes_total: 3,
    episodes_completed: 3,
    episodes_failed: 0,
    processing_time_seconds: 45.5,
    error_message: null,
    success_rate: 100,
    is_complete: true,
    ...overrides,
  }
}

export function createPendingBriefing(overrides: Partial<Briefing> = {}): Briefing {
  return createBriefing({
    status: 'pending' as BriefingStatus,
    episodes_completed: 0,
    success_rate: 0,
    is_complete: false,
    file_path: null,
    processing_time_seconds: null,
    ...overrides,
  })
}

export function createInProgressBriefing(overrides: Partial<Briefing> = {}): Briefing {
  return createBriefing({
    status: 'in_progress' as BriefingStatus,
    episodes_completed: 1,
    episodes_total: 3,
    success_rate: 33.3,
    is_complete: false,
    file_path: null,
    processing_time_seconds: null,
    ...overrides,
  })
}

export function createFailedBriefing(overrides: Partial<Briefing> = {}): Briefing {
  return createBriefing({
    status: 'failed' as BriefingStatus,
    episodes_completed: 0,
    episodes_failed: 3,
    success_rate: 0,
    is_complete: false,
    error_message: 'Processing failed due to network error',
    ...overrides,
  })
}

export function createPreviewEpisode(overrides: Partial<BriefingPreviewEpisode> = {}): BriefingPreviewEpisode {
  return {
    episode_id: 'ep-123',
    episode_title: 'Episode Title',
    episode_slug: 'episode-title',
    podcast_id: 'podcast-123',
    podcast_title: 'Podcast Title',
    podcast_slug: 'podcast-title',
    state: 'summarized',
    pub_date: '2026-01-25T10:00:00Z',
    ...overrides,
  }
}

export function createDashboardStats(overrides: Partial<DashboardStats> = {}): DashboardStats {
  return {
    podcasts_tracked: 5,
    episodes_total: 100,
    episodes_processed: 75,
    episodes_pending: 25,
    pipeline: {
      discovered: 10,
      downloaded: 5,
      downsampled: 5,
      transcribed: 3,
      cleaned: 2,
      summarized: 75,
    },
    ...overrides,
  }
}
