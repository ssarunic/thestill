import { useMemo, useState, useCallback, useEffect, lazy, Suspense } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useEpisode, useEpisodeTranscript, useEpisodeSummary, useEpisodeEntities, useRelatedEpisodes, useEpisodeTranscriptWords } from '../hooks/useApi'
import { useReadingPosition } from '../hooks/useReadingPosition'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import { usePersistedBoolean } from '../hooks/useAutoScrollFollow'

// Lazy load heavy markdown viewer components
const TranscriptViewer = lazy(() => import('../components/TranscriptViewer'))
const SegmentedTranscriptViewer = lazy(() => import('../components/SegmentedTranscriptViewer'))
const SummaryViewer = lazy(() => import('../components/SummaryViewer'))
import ExpandableDescription from '../components/ExpandableDescription'
import { EpisodeNumber } from '../components/EpisodeNumber'
import { ExplicitBadge } from '../components/ExplicitBadge'
import PipelineActionButton from '../components/PipelineActionButton'
import FailureBanner from '../components/FailureBanner'
import ShareButton from '../components/ShareButton'
import KeyEntitiesStrip from '../components/episode-entities/KeyEntitiesStrip'
import EntityRail from '../components/episode-entities/EntityRail'
import EntityFilterBar from '../components/episode-entities/EntityFilterBar'
import MentionDensityTimeline from '../components/episode-entities/MentionDensityTimeline'
import EntityBranchProgress from '../components/EntityBranchProgress'
import type { PipelineStage, FailureType, EntityType, EpisodeEntity, MentionLite } from '../api/types'

type Tab = 'transcript' | 'summary'
type TranscriptSubTab = 'segmented' | 'legacy' | 'shadow'

const stateColors: Record<string, string> = {
  discovered: 'bg-gray-100 text-gray-600',
  downloaded: 'bg-blue-100 text-blue-700',
  downsampled: 'bg-indigo-100 text-indigo-700',
  transcribed: 'bg-purple-100 text-purple-700',
  cleaned: 'bg-amber-100 text-amber-700',
  summarized: 'bg-green-100 text-green-700',
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Unknown date'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}

export default function EpisodeDetail() {
  const { podcastSlug, episodeSlug } = useParams<{ podcastSlug: string; episodeSlug: string }>()
  const [activeTab, setActiveTab] = useState<Tab>('summary')
  const [transcriptSubTab, setTranscriptSubTab] = useState<TranscriptSubTab>('segmented')
  const queryClient = useQueryClient()
  const player = usePlayer()

  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(podcastSlug!, episodeSlug!)
  const { data: transcriptData, isLoading: transcriptLoading } = useEpisodeTranscript(podcastSlug!, episodeSlug!)
  const { data: summaryData, isLoading: summaryLoading } = useEpisodeSummary(podcastSlug!, episodeSlug!)

  // Spec #38 karaoke wipe. Chip state is persisted at the parent level so
  // a single ``usePersistedBoolean`` drives both the chip checkbox and the
  // gated ``useEpisodeTranscriptWords`` call. ``data === null`` is the
  // 404 sentinel — the chip then renders disabled-with-tooltip and the
  // viewer falls back to segment-level highlighting.
  const [karaokeChipOn, setKaraokeChipOn] = usePersistedBoolean('thestill:transcript:karaoke', false)
  const karaokeWordsQuery = useEpisodeTranscriptWords(
    podcastSlug!,
    episodeSlug!,
    karaokeChipOn,
  )
  const karaokeUnavailable = karaokeChipOn && karaokeWordsQuery.isFetched && karaokeWordsQuery.data === null
  const karaokeEffectivelyOn = karaokeChipOn && !karaokeUnavailable
  const handleKaraokeToggle = useCallback(() => setKaraokeChipOn(!karaokeChipOn), [karaokeChipOn, setKaraokeChipOn])

  // When the karaoke chip was carried over from a prior episode (the
  // pref is global, not per-episode) but the new episode lacks word
  // timestamps, auto-clear the persisted ``true`` so the next episode
  // that *does* have words starts unchecked. Without this, the chip
  // would render checked-and-disabled with no way to interact with it,
  // since ``disabled`` blocks the onChange handler.
  useEffect(() => {
    if (karaokeUnavailable) setKaraokeChipOn(false)
  }, [karaokeUnavailable, setKaraokeChipOn])

  // Reading position persistence - auto-restores when episode ID is available
  useReadingPosition(episodeData?.episode?.id)

  const episode = episodeData?.episode

  // Spec #28 §5.2 — episode-page entity UX. One fetch feeds the strip,
  // rail, inline highlights, filter bar, and timeline.
  const { data: entitiesData } = useEpisodeEntities(episode?.id ?? null)
  const entities = entitiesData?.entities ?? []

  // Spec #28 §5.2 — "Related episodes" rail. Independent fetch (the
  // backend computes a centroid over chunk embeddings) so the rail can
  // surface related episodes even when no entities were extracted.
  const { data: relatedData, isLoading: relatedLoading } = useRelatedEpisodes(episode?.id ?? null)
  const relatedEpisodes = relatedData?.episodes ?? []

  const [hiddenEntityTypes, setHiddenEntityTypes] = useState<Set<EntityType>>(() => new Set())
  const [filterEntityIds, setFilterEntityIds] = useState<Set<string>>(() => new Set())
  const [focusedEntityId, setFocusedEntityId] = useState<string | null>(null)

  const visibleEntities = useMemo(
    () => entities.filter((e) => !hiddenEntityTypes.has(e.entity.type)),
    [entities, hiddenEntityTypes],
  )

  const entitiesById = useMemo(() => {
    const m = new Map<string, EpisodeEntity>()
    for (const e of visibleEntities) m.set(e.entity.id, e)
    return m
  }, [visibleEntities])

  const mentionsBySegmentId = useMemo(() => {
    const m = new Map<number, MentionLite[]>()
    for (const e of visibleEntities) {
      for (const mention of e.mentions) {
        const list = m.get(mention.segment_id) ?? []
        list.push(mention)
        m.set(mention.segment_id, list)
      }
    }
    return m
  }, [visibleEntities])

  // When an entity filter is active, derive the set of segment ids
  // that should remain visible in the transcript viewer. Pure
  // client-side filter — `mentions[].segment_id` already carries
  // everything we need.
  const visibleSegmentIds = useMemo(() => {
    if (filterEntityIds.size === 0) return null
    const ids = new Set<number>()
    for (const e of entities) {
      if (!filterEntityIds.has(e.entity.id)) continue
      for (const m of e.mentions) ids.add(m.segment_id)
    }
    return ids
  }, [entities, filterEntityIds])

  const toggleEntityType = useCallback((type: EntityType) => {
    setHiddenEntityTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }, [])

  const toggleEntityFilter = useCallback((entityId: string) => {
    setFilterEntityIds((prev) => {
      const next = new Set(prev)
      if (next.has(entityId)) next.delete(entityId)
      else next.add(entityId)
      return next
    })
  }, [])

  const clearEntityFilter = useCallback(() => setFilterEntityIds(new Set()), [])
  const handleSegmentSeek = useCallback(
    (seconds: number) => {
      if (!episode) return
      if (player.isCurrent(episode.id)) {
        player.seek(seconds)
        if (!player.isPlaying) player.resume()
        return
      }
      player.play(
        {
          episodeId: episode.id,
          podcastSlug: podcastSlug!,
          episodeSlug: episodeSlug!,
          title: episode.title,
          podcastTitle: episode.podcast_title,
          audioUrl: episode.audio_url,
          artworkUrl: episode.image_url ?? episode.podcast_image_url,
          durationHint: episode.duration,
        },
        { startAt: seconds },
      )
    },
    [episode, podcastSlug, episodeSlug, player],
  )

  // Handle task completion - refresh relevant data
  const handleTaskComplete = useCallback((stage: PipelineStage) => {
    // Always refresh episode data to get updated state
    queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug] })

    // Refresh transcript after clean stage completes
    if (stage === 'clean') {
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug, 'transcript'] })
    }

    // Refresh summary after summarize stage completes
    if (stage === 'summarize') {
      queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug, 'summary'] })
    }
  }, [queryClient, podcastSlug, episodeSlug])

  if (episodeError) {
    return (
      <div className="text-center py-12">
        <div className="bg-red-50 border border-red-200 rounded-lg p-6 max-w-md mx-auto">
          <h2 className="text-red-700 font-medium mb-2">Error loading episode</h2>
          <p className="text-red-600 text-sm">{episodeError.message}</p>
          <Link to="/podcasts" className="mt-4 inline-block text-primary-600 hover:underline">
            ← Back to podcasts
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <nav className="text-sm flex flex-wrap items-center gap-1">
        <Link to="/podcasts" className="text-gray-500 hover:text-gray-700">Podcasts</Link>
        <span className="text-gray-400">/</span>
        <Link to={`/podcasts/${podcastSlug}`} className="text-gray-500 hover:text-gray-700 truncate max-w-[120px] sm:max-w-none">{episodeLoading ? '...' : episode?.podcast_title}</Link>
        <span className="text-gray-400 hidden sm:inline">/</span>
        <span className="text-gray-900 truncate max-w-[150px] sm:max-w-none hidden sm:inline">{episodeLoading ? '...' : episode?.title}</span>
      </nav>

      {/* Header */}
      {episodeLoading ? (
        <div className="animate-pulse bg-white rounded-lg border border-gray-200 p-4 sm:p-6 space-y-4">
          {/* Match real header layout: artwork + title area */}
          <div className="flex flex-col sm:flex-row sm:items-start gap-4">
            <div className="w-20 h-20 sm:w-24 sm:h-24 bg-gray-200 rounded-lg flex-shrink-0 mx-auto sm:mx-0 aspect-square" />
            <div className="flex-1 space-y-2 text-center sm:text-left">
              <div className="h-7 bg-gray-200 rounded w-3/4 mx-auto sm:mx-0" />
              <div className="h-5 bg-gray-200 rounded w-1/2 mx-auto sm:mx-0" />
            </div>
          </div>
          {/* Meta info */}
          <div className="h-5 bg-gray-200 rounded w-1/3" />
          {/* Pipeline button area */}
          <div className="border-t border-gray-100 pt-4">
            <div className="h-10 bg-gray-200 rounded w-40" />
          </div>
          {/* Audio player area */}
          <div className="border-t border-gray-100 pt-4">
            <div className="h-12 bg-gray-200 rounded" />
          </div>
        </div>
      ) : episode ? (
        <div className="bg-white rounded-lg border border-gray-200 p-4 sm:p-6 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-start gap-4">
            {/* Episode/Podcast artwork - prioritize episode artwork, fall back to podcast artwork */}
            {(episode.image_url || episode.podcast_image_url) ? (
              <img
                src={episode.image_url || episode.podcast_image_url || ''}
                alt={`${episode.title} artwork`}
                width={96}
                height={96}
                loading="eager"
                className="w-20 h-20 sm:w-24 sm:h-24 rounded-lg object-cover flex-shrink-0 mx-auto sm:mx-0 aspect-square"
              />
            ) : (
              <div className="w-20 h-20 sm:w-24 sm:h-24 bg-gradient-to-br from-primary-100 to-secondary-100 rounded-lg flex items-center justify-center flex-shrink-0 mx-auto sm:mx-0 aspect-square">
                <svg className="w-8 h-8 sm:w-10 sm:h-10 text-primary-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                </svg>
              </div>
            )}
            <div className="flex-1 flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2 sm:gap-4 text-center sm:text-left">
              <div>
                <h1 className="text-xl sm:text-2xl font-bold text-gray-900">{episode.title}</h1>
                <p className="text-gray-600 mt-1">{episode.podcast_title}</p>
              </div>
              <span className={`px-3 py-1 rounded-full text-sm font-medium self-center sm:self-start ${stateColors[episode.state]}`}>
                {episode.state === 'summarized' ? 'Ready' : episode.state.charAt(0).toUpperCase() + episode.state.slice(1)}
              </span>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:gap-4 text-sm text-gray-500">
            <EpisodeNumber
              seasonNumber={episode.season_number}
              episodeNumber={episode.episode_number}
            />
            <ExplicitBadge explicit={episode.explicit} />
            <span>{formatDate(episode.pub_date)}</span>
            {episode.duration_formatted && (
              <>
                <span className="hidden sm:inline">•</span>
                <span>{episode.duration_formatted}</span>
              </>
            )}
            <span className="hidden sm:inline">•</span>
            <ShareButton
              title={`${episode.title} - ${episode.podcast_title}`}
              url={window.location.href}
            />
            {/* Show Notes link */}
            {episode.website_url && (
              <>
                <span className="hidden sm:inline">•</span>
                <a
                  href={episode.website_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-800 hover:underline"
                >
                  Show Notes
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </a>
              </>
            )}
          </div>

          {/* Failure Banner */}
          {episode.is_failed && episode.failed_at_stage && (
            <div className="border-t border-gray-100 pt-4">
              <FailureBanner
                episodeId={episode.id}
                failedAtStage={episode.failed_at_stage}
                failureReason={episode.failure_reason ?? null}
                failureType={(episode.failure_type as FailureType) ?? null}
                failedAt={episode.failed_at ?? null}
                onRetrySuccess={() => {
                  queryClient.invalidateQueries({ queryKey: ['episodes', podcastSlug, episodeSlug] })
                }}
              />
            </div>
          )}

          {/* Pipeline Action Button */}
          {!episode.is_failed && episode.state !== 'summarized' && (
            <div className="border-t border-gray-100 pt-4">
              <PipelineActionButton
                podcastSlug={podcastSlug!}
                episodeSlug={episodeSlug!}
                episodeId={episode.id}
                episodeState={episode.state}
                onTaskComplete={handleTaskComplete}
              />
            </div>
          )}

          {/* Play button — delegates transport to the floating mini-player */}
          <div className="border-t border-gray-100 pt-4">
            {(() => {
              const isCurrent = player.isCurrent(episode.id)
              const isPlaying = isCurrent && player.isPlaying
              const isLoading = isCurrent && player.isLoading
              const handleClick = () => {
                if (isCurrent) {
                  player.toggle()
                } else {
                  player.play({
                    episodeId: episode.id,
                    podcastSlug: podcastSlug!,
                    episodeSlug: episodeSlug!,
                    title: episode.title,
                    podcastTitle: episode.podcast_title,
                    audioUrl: episode.audio_url,
                    artworkUrl: episode.image_url ?? episode.podcast_image_url,
                    durationHint: episode.duration,
                  })
                }
              }
              return (
                <button
                  type="button"
                  onClick={handleClick}
                  disabled={isLoading && !isPlaying}
                  aria-label={isPlaying ? 'Pause' : 'Play'}
                  className="inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-primary-900 text-white font-medium hover:bg-primary-800 active:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {isLoading && !isPlaying ? (
                    <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                    </svg>
                  ) : isPlaying ? (
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
                      <rect x="6" y="5" width="4" height="14" rx="1" />
                      <rect x="14" y="5" width="4" height="14" rx="1" />
                    </svg>
                  ) : (
                    <svg className="w-5 h-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
                      <path d="M8 5v14l11-7z" />
                    </svg>
                  )}
                  <span>{isPlaying ? 'Pause' : isCurrent ? 'Resume' : 'Play episode'}</span>
                </button>
              )
            })()}
          </div>

          {/* Spec #28 §"Failure isolation" — entity branch is its own
              status row, independent of the user chain. Renders only
              when entity-branch tasks exist for this episode. */}
          <div className="border-t border-gray-100 pt-4">
            <EntityBranchProgress episodeId={episode.id} />
          </div>

          {(episode.description_html || episode.description) && (
            <div className="border-t border-gray-100 pt-4">
              <ExpandableDescription html={episode.description_html || episode.description} maxLines={3} />
            </div>
          )}
        </div>
      ) : null}

      {/* Spec #28 §5.2 — Key entities strip, above the fold. Empty
          state (zero entities) hides itself. */}
      {entities.length > 0 && (
        <KeyEntitiesStrip
          entities={entities}
          hiddenTypes={hiddenEntityTypes}
          onToggleType={toggleEntityType}
          onSeek={handleSegmentSeek}
        />
      )}

      {/* Content Tabs + right rail. lg+ becomes a 2-col grid; below lg
          the rail wraps under the panel. */}
      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_18rem] lg:gap-6">
        <div className="bg-white rounded-lg border border-gray-200 min-h-[400px]">
          {/* Tab Headers */}
          <div className="border-b border-gray-200">
            <nav className="flex">
              <button
                onClick={() => setActiveTab('summary')}
                className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  activeTab === 'summary'
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Summary
                {episode?.has_summary && (
                  <span className="ml-2 w-2 h-2 inline-block rounded-full bg-green-400" />
                )}
              </button>
              <button
                onClick={() => setActiveTab('transcript')}
                className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  activeTab === 'transcript'
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                Transcript
                {episode?.has_transcript && (
                  <span className="ml-2 w-2 h-2 inline-block rounded-full bg-green-400" />
                )}
              </button>
            </nav>
          </div>

          {/* Tab Content */}
          <div className="p-4 sm:p-6">
            <Suspense fallback={
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
              </div>
            }>
              {activeTab === 'summary' ? (
                <SummaryViewer
                  content={summaryData?.content ?? ''}
                  isLoading={summaryLoading}
                  available={summaryData?.available}
                  episodeState={episode?.state}
                />
              ) : (
                <TranscriptPanel
                  transcriptData={transcriptData}
                  transcriptLoading={transcriptLoading}
                  episodeState={episode?.state}
                  episodeId={episode?.id ?? null}
                  audioUrl={episode?.audio_url ?? null}
                  onSegmentSeek={handleSegmentSeek}
                  subTab={transcriptSubTab}
                  onSubTabChange={setTranscriptSubTab}
                  entitiesById={entitiesById}
                  mentionsBySegmentId={mentionsBySegmentId}
                  visibleSegmentIds={visibleSegmentIds}
                  focusedEntityId={focusedEntityId}
                  onFocusEntity={setFocusedEntityId}
                  entityFilterBar={
                    entities.length > 0 ? (
                      <EntityFilterBar
                        entities={entities}
                        selectedEntityIds={filterEntityIds}
                        onToggle={toggleEntityFilter}
                        onClear={clearEntityFilter}
                      />
                    ) : null
                  }
                  karaokeEnabled={karaokeEffectivelyOn}
                  karaokeWords={karaokeWordsQuery.data}
                  karaokeChipChecked={karaokeChipOn}
                  karaokeChipDisabled={karaokeUnavailable}
                  onKaraokeToggle={handleKaraokeToggle}
                />
              )}
            </Suspense>
          </div>
        </div>

        {/* Right rail — only on lg+; collapses below the breakpoint
            (the strip carries the gist on mobile). Shown when there are
            entities OR related episodes so the rail surfaces even on
            episodes without entity extraction. */}
        {(entities.length > 0 || relatedEpisodes.length > 0 || relatedLoading) && (
          <div className="hidden lg:block">
            <div className="sticky top-4 space-y-4 rounded-lg border border-gray-200 bg-white p-4">
              <EntityRail
                entities={entities}
                onSeek={handleSegmentSeek}
                onFocusEntity={setFocusedEntityId}
                relatedEpisodes={relatedEpisodes}
                relatedLoading={relatedLoading}
              />
            </div>
          </div>
        )}
      </div>

      {/* Mention density timeline — fixed-position strip beside the
          MiniPlayer when this episode is the current track. Only
          rendered on md+ screens (hides itself when there's no room). */}
      {episode && entities.length > 0 && episode.duration && (
        <PlayerScopedTimeline
          episodeId={episode.id}
          entities={entities}
          durationSeconds={episode.duration}
          onSeek={handleSegmentSeek}
        />
      )}
    </div>
  )
}

interface PlayerScopedTimelineProps {
  episodeId: string
  entities: EpisodeEntity[]
  durationSeconds: number
  onSeek: (seconds: number) => void
}

// Renders the MentionDensityTimeline only when the global player is
// actually on this episode. We co-locate the gating here rather than
// inside MentionDensityTimeline so the latter stays pure UI.
function PlayerScopedTimeline({ episodeId, entities, durationSeconds, onSeek }: PlayerScopedTimelineProps) {
  const player = usePlayer()
  // Subscribing to the high-frequency time context here is a no-op
  // outside React's rendering pass; it just ensures the component
  // re-renders whenever playback changes — useful in case we add
  // timeline cursor markers later.
  usePlayerTime()
  if (!player.isCurrent(episodeId)) return null
  return (
    <MentionDensityTimeline entities={entities} durationSeconds={durationSeconds} onSeek={onSeek} />
  )
}

/**
 * Transcript panel — renders the "Segmented / Legacy blended / Shadow"
 * sub-tab toggle (spec #18 Phase D) and the chosen viewer beneath it.
 *
 * The toggle is only shown when more than one variant is available.
 * "Segmented" is the default when present; otherwise "Legacy blended"
 * is the fallback. "Shadow" appears only when the cleanup processor
 * wrote a dual-pipeline debug file.
 */
interface TranscriptPanelProps {
  transcriptData: import('../api/types').ContentResponse | undefined
  transcriptLoading: boolean
  episodeState: string | undefined
  episodeId: string | null
  audioUrl: string | null
  onSegmentSeek: (seconds: number) => void
  subTab: TranscriptSubTab
  onSubTabChange: (next: TranscriptSubTab) => void
  // Spec #28 §5.2 — episode-page entity UX. All optional so a viewer
  // mounted without entity data (legacy episodes, tests) keeps working.
  entitiesById?: Map<string, EpisodeEntity>
  mentionsBySegmentId?: Map<number, MentionLite[]>
  visibleSegmentIds?: Set<number> | null
  focusedEntityId?: string | null
  onFocusEntity?: (entityId: string) => void
  // Slot for the filter bar — rendered above the segmented viewer so
  // it shares the panel's padding and lives under the sub-tab toggle.
  entityFilterBar?: React.ReactNode
  // Spec #38 karaoke wipe — threaded through from EpisodeDetail which
  // owns the chip state + the words query.
  karaokeEnabled?: boolean
  karaokeWords?: import('../api/types').KaraokeWordsByEpisode | null
  karaokeChipChecked?: boolean
  karaokeChipDisabled?: boolean
  onKaraokeToggle?: () => void
}

// Passively probe an audio URL for its duration without playing it. We
// rely on the browser requesting only the MP3 metadata (preload:'metadata')
// so the full file isn't downloaded. `null` while unknown.
function useAudioDuration(url: string | null): number | null {
  const [duration, setDuration] = useState<number | null>(null)
  useEffect(() => {
    setDuration(null)
    if (!url) return
    const audio = new Audio()
    audio.preload = 'metadata'
    audio.src = url
    const onMeta = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) {
        setDuration(audio.duration)
      }
    }
    audio.addEventListener('loadedmetadata', onMeta)
    return () => {
      audio.removeEventListener('loadedmetadata', onMeta)
      // Cancel any in-flight metadata fetch when the component unmounts
      // or the url changes — avoids leaving zombie network requests.
      audio.src = ''
    }
  }, [url])
  return duration
}

// VBR MP3 duration differs by 1–3s between decoders, and hosts occasionally
// rotate small sting/bumper audio. Anything under this threshold is almost
// certainly noise, not a real ad shift worth alarming the user about.
// A real pre/mid-roll ad is ≥15s, so 15s is the smallest "caught every
// real issue, ignored every false positive" threshold in practice.
const DRIFT_THRESHOLD_SECONDS = 15

// Decide whether to show a drift warning. Three states:
// - 'aligned': source duration matches live audio within threshold
// - 'drifted': they disagree — timestamps may not land where expected
// - 'unknown': transcript predates source-duration recording, OR live
//   audio metadata hasn't resolved yet; we don't warn on unknown to avoid
//   false positives on legacy transcripts where drift may also be fine
function classifyDrift(
  sourceDuration: number | null | undefined,
  liveDuration: number | null,
): { state: 'aligned' | 'drifted' | 'unknown'; deltaSeconds: number | null } {
  if (sourceDuration == null) return { state: 'unknown', deltaSeconds: null }
  if (liveDuration == null) return { state: 'unknown', deltaSeconds: null }
  const delta = liveDuration - sourceDuration
  if (Math.abs(delta) < DRIFT_THRESHOLD_SECONDS) {
    return { state: 'aligned', deltaSeconds: delta }
  }
  return { state: 'drifted', deltaSeconds: delta }
}

function formatDeltaSeconds(seconds: number): string {
  const abs = Math.abs(seconds)
  const mm = Math.floor(abs / 60)
  const ss = Math.round(abs % 60)
  const pad = (n: number) => n.toString().padStart(2, '0')
  const sign = seconds >= 0 ? '+' : '−'
  return `${sign}${mm > 0 ? `${mm}m ` : ''}${pad(ss)}s`
}

function TranscriptPanel({
  transcriptData,
  transcriptLoading,
  episodeState,
  episodeId,
  audioUrl,
  onSegmentSeek,
  subTab,
  onSubTabChange,
  entitiesById,
  mentionsBySegmentId,
  visibleSegmentIds,
  focusedEntityId,
  onFocusEntity,
  entityFilterBar,
  karaokeEnabled,
  karaokeWords,
  karaokeChipChecked,
  karaokeChipDisabled,
  onKaraokeToggle,
}: TranscriptPanelProps) {
  const hasSegments = !!transcriptData?.segments
  const hasLegacy = !!transcriptData?.content && transcriptData.content.length > 0
  const hasShadow = !!transcriptData?.shadow
  const liveAudioDuration = useAudioDuration(audioUrl)
  const drift = classifyDrift(
    transcriptData?.segments?.transcript_source_duration_s,
    liveAudioDuration,
  )

  // Clamp the selected sub-tab to one that's actually available. Avoids
  // flashing an empty panel when the user previously viewed a segmented
  // transcript and then navigates to a Parakeet-fallback episode where
  // only legacy exists.
  const availableSubTabs: TranscriptSubTab[] = []
  if (hasSegments) availableSubTabs.push('segmented')
  if (hasLegacy) availableSubTabs.push('legacy')
  if (hasShadow) availableSubTabs.push('shadow')
  const effectiveSubTab: TranscriptSubTab = availableSubTabs.includes(subTab)
    ? subTab
    : availableSubTabs[0] ?? 'legacy'

  const showToggle = availableSubTabs.length >= 2

  return (
    <div>
      {drift.state === 'drifted' && drift.deltaSeconds != null && (
        <div
          role="status"
          className="mb-4 flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
        >
          <svg
            className="mt-0.5 w-5 h-5 shrink-0 text-amber-600"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
            />
          </svg>
          <div>
            <p className="font-medium">Timestamps may have drifted</p>
            <p className="mt-0.5 text-amber-800/90">
              The audio served for this episode is {formatDeltaSeconds(drift.deltaSeconds)}{' '}
              {drift.deltaSeconds > 0 ? 'longer' : 'shorter'} than when it was transcribed
              (likely dynamic ads inserted by the host). Clicking a segment will seek
              to the displayed time, but the audio at that position may not match.
            </p>
          </div>
        </div>
      )}
      {showToggle && (
        <div className="flex gap-1 mb-4 p-1 bg-gray-100 rounded-lg w-fit">
          {hasSegments && (
            <SubTabButton
              label="Segmented"
              active={effectiveSubTab === 'segmented'}
              onClick={() => onSubTabChange('segmented')}
            />
          )}
          {hasLegacy && (
            <SubTabButton
              label="Legacy blended"
              active={effectiveSubTab === 'legacy'}
              onClick={() => onSubTabChange('legacy')}
            />
          )}
          {hasShadow && (
            <SubTabButton
              label={`Shadow (${transcriptData!.shadow!.pipeline})`}
              active={effectiveSubTab === 'shadow'}
              onClick={() => onSubTabChange('shadow')}
            />
          )}
        </div>
      )}

      {effectiveSubTab === 'segmented' && transcriptData?.segments ? (
        <>
          {entityFilterBar && <div className="mb-3">{entityFilterBar}</div>}
          <SegmentedTranscriptViewer
            transcript={transcriptData.segments}
            episodeId={episodeId}
            onSeekRequest={onSegmentSeek}
            entitiesById={entitiesById}
            mentionsBySegmentId={mentionsBySegmentId}
            visibleSegmentIds={visibleSegmentIds}
            focusedEntityId={focusedEntityId}
            onFocusEntity={onFocusEntity}
            karaokeEnabled={karaokeEnabled}
            karaokeWords={karaokeWords}
            karaokeChipChecked={karaokeChipChecked}
            karaokeChipDisabled={karaokeChipDisabled}
            onKaraokeToggle={onKaraokeToggle}
          />
        </>
      ) : effectiveSubTab === 'shadow' && transcriptData?.shadow ? (
        <TranscriptViewer
          content={transcriptData.shadow.content}
          isLoading={false}
          available
          episodeState={episodeState}
          transcriptType="cleaned"
        />
      ) : (
        <TranscriptViewer
          content={transcriptData?.content ?? ''}
          isLoading={transcriptLoading}
          available={transcriptData?.available}
          episodeState={episodeState}
          transcriptType={transcriptData?.transcript_type}
        />
      )}
    </div>
  )
}

interface SubTabButtonProps {
  label: string
  active: boolean
  onClick: () => void
}

function SubTabButton({ label, active, onClick }: SubTabButtonProps) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
        active
          ? 'bg-white text-primary-700 shadow-sm'
          : 'text-gray-600 hover:text-gray-900'
      }`}
    >
      {label}
    </button>
  )
}
