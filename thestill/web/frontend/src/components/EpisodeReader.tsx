import { useMemo, useState, useCallback, useEffect, useRef, lazy, Suspense, type RefObject } from 'react'
import { useParams, Link, useSearchParams, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useEpisode, useEpisodeTranscript, useEpisodeSummary, useEpisodeEntities, useRelatedEpisodes, useEpisodeTranscriptWords, useMarkInboxReadOnView, useEpisodeLiveRefresh, useEpisodeTasks } from '../hooks/useApi'
import { useReadingPosition } from '../hooks/useReadingPosition'
import { usePlayer } from '../contexts/PlayerContext'
import { usePersistedBoolean } from '../hooks/useAutoScrollFollow'
import { useDeepLinkScrollTarget } from '../hooks/useDeepLinkScrollTarget'

// Lazy load heavy markdown viewer components
const TranscriptViewer = lazy(() => import('./TranscriptViewer'))
const SegmentedTranscriptViewer = lazy(() => import('./SegmentedTranscriptViewer'))
const SummaryViewer = lazy(() => import('./SummaryViewer'))

// Episode states at which the entity branch has had a chance to run — it
// is enqueued off the `clean` stage (thestill/core/queue_manager.py).
const ENTITY_READY_STATES: ReadonlySet<string> = new Set<EpisodeState>(['cleaned', 'summarized'])
const ACTIVE_TASK_STATUSES: ReadonlySet<EpisodeTask['status']> = new Set([
  'pending',
  'processing',
  'retry_scheduled',
])
import TheaterSurface from './TheaterSurface'
import PipelineActionButton from './PipelineActionButton'
import FailureBanner from './FailureBanner'
import Panel from './Panel'
import EpisodeHeader, { EpisodeHeaderSkeleton } from './episode-header/EpisodeHeader'
import People from './episode-header/People'
import { buildEpisodeInformationRows } from './episode-header/episodeInformation'
import DefinitionList from './DefinitionList'
import { useCollapsingHeader } from '../hooks/useCollapsingHeader'
import type { CollapsedHeaderState } from './CollapsedEpisodeBar'
import KeyEntitiesStrip from './episode-entities/KeyEntitiesStrip'
import EntityRail from './episode-entities/EntityRail'
import EntityFilterBar from './episode-entities/EntityFilterBar'
import EntityBranchProgress from './EntityBranchProgress'
import type { FailureType, EntityType, EpisodeEntity, EpisodeState, EpisodeTask, MentionLite, SummaryCitation } from '../api/types'
import { ENTITY_BRANCH_STAGES } from '../constants/stages'

type Tab = 'transcript' | 'summary'
type SegmentScrollTarget = { segmentId: number; nonce: number }

function normalizeLanguageCode(language: string | null | undefined): string | undefined {
  const primary = language?.trim().toLowerCase().split(/[-_]/, 1)[0]
  return primary && /^[a-z]{2,3}$/.test(primary) ? primary : undefined
}

function getBrowserLanguage(): string {
  return normalizeLanguageCode(typeof navigator === 'undefined' ? undefined : navigator.language) ?? 'en'
}

export interface EpisodeReaderProps {
  // Spec #52 — when rendered inside the reader overlay, scrolling happens in
  // the overlay panel's own div rather than the window. Reading-position
  // persistence needs to know which one to observe.
  scrollContainerRef?: RefObject<HTMLElement | null>
  // Spec #76 §3.7 — the reader detects when its title scrolls away and
  // reports what a collapsed bar needs; the host renders the bar in its own
  // chrome (the overlay swaps its header, the page pins a sticky bar).
  // Called with ``null`` when the title is back in view or on unmount.
  onCollapsedHeaderChange?: (state: CollapsedHeaderState | null) => void
  // Height of fixed chrome above the scroll area (the mobile shell header in
  // page mode); the title counts as gone once it slides under it.
  collapseTopOffset?: number
}

/**
 * Spec #52 — the episode reader shared by the standalone page
 * (EpisodeDetail = breadcrumb + reader) and the inbox overlay
 * (EpisodeReaderOverlay = chrome + reader). Owns its own data fetching
 * keyed off route params so both modes behave identically — including
 * spec #29 read-on-view marking.
 */
export default function EpisodeReader({
  scrollContainerRef,
  onCollapsedHeaderChange,
  collapseTopOffset = 0,
}: EpisodeReaderProps) {
  const { podcastSlug, episodeSlug } = useParams<{ podcastSlug: string; episodeSlug: string }>()
  const [searchParams] = useSearchParams()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const player = usePlayer()

  // The active tab lives in the URL (`?view=transcript`) rather than local
  // state, so a citation jump can push a history entry and browser Back
  // returns to the summary the reader was on. Manual tab clicks replace (no
  // history spam); `location.state` is threaded through every navigation so
  // the spec #52 overlay's `backgroundLocation` survives the change.
  const activeTab: Tab = searchParams.get('view') === 'transcript' ? 'transcript' : 'summary'
  const requestedSummaryLanguage = normalizeLanguageCode(searchParams.get('lang'))
  const defaultSummaryLanguage = getBrowserLanguage()

  // Summary and transcript share one scroll container. Remember the summary's
  // scroll offset when leaving it and restore it when returning (browser Back
  // from a citation jump, or a manual tab toggle), so the reader lands back
  // where you were reading instead of at the transcript's leftover offset.
  const summaryScrollRef = useRef(0)
  const getScrollTop = useCallback(() => {
    const el = scrollContainerRef?.current
    return el ? el.scrollTop : window.scrollY
  }, [scrollContainerRef])
  const setScrollTop = useCallback(
    (top: number) => {
      const el = scrollContainerRef?.current
      if (el) el.scrollTo({ top, behavior: 'instant' })
      else window.scrollTo({ top, behavior: 'instant' })
    },
    [scrollContainerRef],
  )

  const setTab = useCallback(
    (tab: Tab, opts?: { push?: boolean }) => {
      if (activeTab === 'summary' && tab !== 'summary') summaryScrollRef.current = getScrollTop()
      const params = new URLSearchParams(searchParams)
      if (tab === 'transcript') params.set('view', 'transcript')
      else params.delete('view')
      const search = params.toString()
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : '' },
        { replace: !opts?.push, state: location.state },
      )
    },
    [activeTab, getScrollTop, navigate, location.pathname, location.state, searchParams],
  )

  // Restore the summary scroll offset when the tab returns to summary from the
  // transcript (Back after a citation jump, or a manual toggle back). Two rAFs
  // let the re-mounted summary content lay out before we scroll.
  const prevTabRef = useRef<Tab>(activeTab)
  useEffect(() => {
    const prev = prevTabRef.current
    prevTabRef.current = activeTab
    if (prev === 'transcript' && activeTab === 'summary') {
      const top = summaryScrollRef.current
      requestAnimationFrame(() => requestAnimationFrame(() => setScrollTop(top)))
    }
  }, [activeTab, setScrollTop])

  // Leaving the summary by a route setTab does not own — a `?view=transcript`
  // push from outside the reader (spec #72 "Open transcript here") — must
  // still return to the right place on Back. Capturing at the transition is
  // too late (the browser has already clamped the offset to the new
  // content), so the summary's offset is tracked while it is on screen.
  useEffect(() => {
    if (activeTab !== 'summary') return
    const target: HTMLElement | Window = scrollContainerRef?.current ?? window
    const record = () => {
      summaryScrollRef.current = getScrollTop()
    }
    target.addEventListener('scroll', record, { passive: true })
    return () => target.removeEventListener('scroll', record)
  }, [activeTab, getScrollTop, scrollContainerRef])

  // Spec #68 D1 — `settled` comes back from `useEpisodeLiveRefresh` below and
  // feeds back in here on the next render, stopping the 5s clock once the
  // episode is terminal *and* its content has actually landed. The one-render
  // lag is deliberate and harmless: an extra tick costs one request, whereas
  // computing terminality before the content queries have reported would stop
  // the clock while the summary was still catching up.
  const [settled, setSettled] = useState(false)
  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(
    podcastSlug!,
    episodeSlug!,
    { live: !settled },
  )
  const { data: transcriptData, isLoading: transcriptLoading } = useEpisodeTranscript(podcastSlug!, episodeSlug!)
  const {
    data: summaryData,
    isLoading: summaryLoading,
    isFetching: summaryFetching,
  } = useEpisodeSummary(podcastSlug!, episodeSlug!, requestedSummaryLanguage)
  const summaryBusy = summaryLoading || summaryFetching
  const podcastLanguage = normalizeLanguageCode(summaryData?.podcast_language)
  const canonicalSummaryLanguage = normalizeLanguageCode(summaryData?.canonical_language)
  const selectedSummaryLanguage = requestedSummaryLanguage ?? normalizeLanguageCode(summaryData?.language) ?? canonicalSummaryLanguage

  // The reader flips between the podcast's original language, the language the
  // canonical artifact is actually stored in, and their own browser default.
  // These can all differ: the pre-#58 corpus holds English summaries of
  // foreign podcasts, so canonical (en) != podcast (hr). Deriving the offered
  // options from all three — rather than assuming canonical == podcast — keeps
  // the canonical reachable (so `selectedSummaryLanguage` always maps to a
  // button) and never hides the toggle from a reader whose locale matches the
  // podcast language but not the stored summary.
  const summaryLanguageOptions = useMemo(() => {
    const options: { code: string; original: boolean }[] = []
    const seen = new Set<string>()
    const add = (code: string | undefined) => {
      if (!code || seen.has(code)) return
      seen.add(code)
      options.push({ code, original: code === podcastLanguage })
    }
    add(podcastLanguage) // original language, marked "(original)"
    add(canonicalSummaryLanguage) // the free, already-stored artifact
    add(defaultSummaryLanguage) // the reader's browser locale
    return options
  }, [podcastLanguage, canonicalSummaryLanguage, defaultSummaryLanguage])
  const showSummaryLanguageToggle = summaryLanguageOptions.length > 1
  const translationInProgress = Boolean(
    summaryFetching
      && requestedSummaryLanguage
      && requestedSummaryLanguage !== canonicalSummaryLanguage
      && !(summaryData?.available_languages ?? []).includes(requestedSummaryLanguage),
  )

  const setSummaryLanguage = useCallback(
    (language: string) => {
      const params = new URLSearchParams(searchParams)
      if (language === canonicalSummaryLanguage) params.delete('lang')
      else params.set('lang', language)
      const search = params.toString()
      navigate(
        { pathname: location.pathname, search: search ? `?${search}` : '' },
        { replace: false, state: location.state },
      )
    },
    [canonicalSummaryLanguage, location.pathname, location.state, navigate, searchParams],
  )

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
  useReadingPosition(episodeData?.episode?.id, scrollContainerRef)

  const episode = episodeData?.episode

  // Spec #29 read tracking — viewing this page while a summary exists is
  // what marks the inbox row read, regardless of how the user got here.
  useMarkInboxReadOnView(episode?.id, summaryData?.available === true)

  // Spec #68 — keep the frozen transcript/summary queries in step with the
  // 5s episode poll as the pipeline advances. Mounted here rather than in
  // `PipelineActionButton` (which unmounts at `summarized`, on the very
  // transition that matters) so the standalone page and the spec #52
  // overlay both stay live off one implementation.
  const live = useEpisodeLiveRefresh({
    podcastSlug,
    episodeSlug,
    episode,
    transcriptAvailable: transcriptData?.available,
    summaryAvailable: summaryData?.available,
  })

  // Render-phase adjustment rather than an effect: React re-renders
  // immediately with the new value instead of committing a frame with the
  // stale one, and it keeps this off the cascading-render path an effect
  // would put it on.
  if (settled !== live.settled) setSettled(live.settled)

  // Spec #68 D2 — one owner for the episode's task list. Both consumers
  // (`PipelineActionButton`, `EntityBranchProgress`) render from this single
  // query rather than each calling the hook: two observers of one cache entry
  // would each keep their own idle counter, and whichever loses a collided
  // fetch would not advance, so the terminal rule would stop being a property
  // of the query.
  const { data: tasksData } = useEpisodeTasks(episode?.id ?? null, {
    contentTerminal: live.contentTerminal,
  })
  const episodeTasks = useMemo(() => tasksData?.tasks ?? [], [tasksData])

  // Spec #28 §5.2 — episode-page entity UX. One fetch feeds the strip,
  // rail, inline highlights, filter bar, and timeline.
  const { data: entitiesData } = useEpisodeEntities(episode?.id ?? null)
  const entities = entitiesData?.entities ?? []

  // Spec #28 §5.2 — "Related episodes" rail. Independent fetch (the
  // backend computes a centroid over chunk embeddings) so the rail can
  // surface related episodes even when no entities were extracted.
  const { data: relatedData, isLoading: relatedLoading } = useRelatedEpisodes(episode?.id ?? null)
  const relatedEpisodes = relatedData?.episodes ?? []

  // The entity branch only runs once the transcript is cleaned, and it can
  // still be queued or mid-flight after that. Either way an empty rail
  // means "not yet" rather than "nothing found"; the rail picks its
  // empty-state copy from this.
  const extractionPending = useMemo(() => {
    if (!episode?.state || !ENTITY_READY_STATES.has(episode.state)) return true
    return episodeTasks.some(
      (t) => ENTITY_BRANCH_STAGES.has(t.stage) && ACTIVE_TASK_STATUSES.has(t.status),
    )
  }, [episode?.state, episodeTasks])

  const [hiddenEntityTypes, setHiddenEntityTypes] = useState<Set<EntityType>>(() => new Set())
  const [filterEntityIds, setFilterEntityIds] = useState<Set<string>>(() => new Set())
  const [focusedEntityId, setFocusedEntityId] = useState<string | null>(null)
  const [citationScrollTarget, setCitationScrollTarget] = useState<SegmentScrollTarget | null>(null)

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

  // One track object for every play entry point on this page (play button,
  // segment seek, theater surface) — includes the spec #61 playback-asset
  // manifest so video episodes start on the video rendition.
  const playerTrack = useMemo(
    () =>
      episode
        ? {
            episodeId: episode.id,
            podcastSlug: podcastSlug!,
            episodeSlug: episodeSlug!,
            title: episode.title,
            podcastTitle: episode.podcast_title,
            audioUrl: episode.audio_url,
            artworkUrl: episode.image_url ?? episode.podcast_image_url,
            durationHint: episode.duration,
            playback: episode.playback ?? null,
          }
        : null,
    [episode, podcastSlug, episodeSlug],
  )

  const handleSegmentSeek = useCallback(
    (seconds: number) => {
      if (!episode || !playerTrack) return
      if (player.isCurrent(episode.id)) {
        player.seek(seconds)
        if (!player.isPlaying) player.resume()
        return
      }
      player.play(playerTrack, { startAt: seconds })
    },
    [episode, playerTrack, player],
  )

  // Spec #76 §3.2 — one derivation feeds the hero's primary action (and,
  // in phase 2, the collapsed bar). ``isPlaying``/``isLoading`` are global
  // player flags, meaningful only when this episode is the loaded track.
  const isCurrent = episode ? player.isCurrent(episode.id) : false
  const isPlaying = isCurrent && player.isPlaying
  const isPlayerLoading = isCurrent && player.isLoading
  const handleTogglePlay = useCallback(() => {
    if (isCurrent) player.toggle()
    else if (playerTrack) player.play(playerTrack)
  }, [isCurrent, player, playerTrack])
  // Spec #62 §6 — audio-kind episodes carrying an episode-level YouTube
  // link enter the YouTube rendition from the action row (the theater
  // mounts once the engine switches). Video-kind episodes get their
  // YouTube entry in the theater menu.
  const showWatchVideo = Boolean(
    playerTrack &&
      episode?.playback?.youtube &&
      episode.playback.kind !== 'video' &&
      !(player.activeEngine === 'youtube' && isCurrent),
  )
  const handleWatchVideo = useCallback(() => {
    if (playerTrack) player.playYouTube(playerTrack)
  }, [player, playerTrack])

  const { titleRef, collapsed } = useCollapsingHeader(scrollContainerRef, collapseTopOffset)
  useEffect(() => {
    if (!onCollapsedHeaderChange) return
    onCollapsedHeaderChange(
      collapsed && episode
        ? {
            title: episode.title,
            artworkUrl: episode.image_url ?? episode.podcast_image_url,
            isPlaying,
            isLoading: isPlayerLoading,
            onTogglePlay: handleTogglePlay,
          }
        : null,
    )
  }, [onCollapsedHeaderChange, collapsed, episode, isPlaying, isPlayerLoading, handleTogglePlay])
  useEffect(() => () => onCollapsedHeaderChange?.(null), [onCollapsedHeaderChange])

  const transcriptSegments = transcriptData?.segments?.segments ?? null

  // Spec #72 §Deep link — `?t=` lands the reader on that segment regardless
  // of the follow toggle: once per history entry, fresh entries only. A POP
  // back to an entry already handled does nothing here, so the saved
  // reading position (useReadingPosition) wins on return.
  const handleDeepLinkTarget = useCallback((segmentId: number) => {
    setCitationScrollTarget((prev) => ({ segmentId, nonce: (prev?.nonce ?? 0) + 1 }))
  }, [])
  useDeepLinkScrollTarget({
    episodeId: episode?.id,
    segments: transcriptSegments,
    offset: transcriptData?.segments?.playback_time_offset_seconds ?? 0,
    onTarget: handleDeepLinkTarget,
  })

  // Spec #76 §3.5 — a People chip for a plain speaker label is a transcript
  // jump, not a playback action: same tab-switch + segment-scroll path as a
  // citation, without the seek. The chip carries its first segment's id.
  const handleSpeakerSelect = useCallback(
    (segmentId: number) => {
      clearEntityFilter()
      if (activeTab !== 'transcript') setTab('transcript', { push: true })
      setCitationScrollTarget((prev) => ({ segmentId, nonce: (prev?.nonce ?? 0) + 1 }))
    },
    [clearEntityFilter, activeTab, setTab],
  )

  const handleSummaryCitation = useCallback(
    (citation: SummaryCitation) => {
      const seconds = citation.target_playback_s ?? citation.cited_playback_s
      handleSegmentSeek(seconds)

      const segmentId = citation.segment_id_hint
      if (segmentId == null) return

      clearEntityFilter()
      // Push a history entry only when actually switching tabs, so browser
      // Back returns to the summary rather than exiting the reader. Already on
      // the transcript → just re-scroll, no extra entry.
      if (activeTab !== 'transcript') setTab('transcript', { push: true })
      setCitationScrollTarget((prev) => ({
        segmentId,
        nonce: (prev?.nonce ?? 0) + 1,
      }))
    },
    [activeTab, setTab, clearEntityFilter, handleSegmentSeek],
  )

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
      {/* Header — spec #76 §3: hero, action row, description (plain
          surface, no card, no dividers), then operator status below the
          primary action (§3.4). */}
      {episodeLoading ? (
        <EpisodeHeaderSkeleton />
      ) : episode ? (
        <>
          <EpisodeHeader
            episode={episode}
            podcastSlug={podcastSlug!}
            titleRef={titleRef}
            playback={{ isCurrent, isPlaying, isLoading: isPlayerLoading, onToggle: handleTogglePlay }}
            showWatchVideo={showWatchVideo}
            onWatchVideo={handleWatchVideo}
            shareUrl={window.location.href}
          />

          {episode.is_failed && episode.failed_at_stage && (
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
          )}

          {!episode.is_failed && episode.state !== 'summarized' && (
            <PipelineActionButton
              podcastSlug={podcastSlug!}
              episodeSlug={episodeSlug!}
              episodeId={episode.id}
              episodeState={episode.state}
              tasks={episodeTasks}
            />
          )}
        </>
      ) : null}

      {/* Spec #61 §2 — theater surface for video episodes: a 16:9 slot
          above the transcript that the global media layer positions the
          stable video node over. Karaoke transcript runs beneath exactly
          as today; clicking a word still seeks. Spec #62 §6 — audio-kind
          episodes with a YouTube link get the theater only once the user
          opts into the YouTube rendition ("Watch video"); leaving it
          returns the reader to plain audio presentation. */}
      {episode &&
        playerTrack &&
        ((episode.playback?.kind === 'video' && episode.playback.video) ||
          (episode.playback?.youtube && player.activeEngine === 'youtube' && player.isCurrent(episode.id))) && (
          <TheaterSurface
            episodeId={episode.id}
            posterUrl={episode.playback.poster_url ?? episode.image_url ?? episode.podcast_image_url}
            track={playerTrack}
          />
        )}

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
        <Panel className="min-h-[400px]">
          {/* Tab Headers */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hairline pr-3">
            <nav className="flex">
              <button
                onClick={() => setTab('summary')}
                className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  activeTab === 'summary'
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-muted hover:text-gray-700'
                }`}
              >
                Summary
              </button>
              <button
                onClick={() => setTab('transcript')}
                className={`flex-1 sm:flex-none px-4 sm:px-6 py-4 sm:py-3 text-sm font-medium border-b-2 -mb-px transition-colors ${
                  activeTab === 'transcript'
                    ? 'border-primary-600 text-primary-600'
                    : 'border-transparent text-muted hover:text-gray-700'
                }`}
              >
                Transcript
              </button>
            </nav>
            {showSummaryLanguageToggle && (
              <div className="flex items-center gap-2">
                {translationInProgress && requestedSummaryLanguage && (
                  <span
                    role="status"
                    aria-live="polite"
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-primary-700"
                  >
                    <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600" />
                    Translating to {requestedSummaryLanguage.toUpperCase()}…
                  </span>
                )}
                <div
                  className="inline-flex items-center rounded-lg border border-hairline bg-page p-0.5 text-xs font-medium"
                  aria-label="Summary language"
                >
                  {summaryLanguageOptions.map(({ code, original }) => (
                    <button
                      key={code}
                      type="button"
                      onClick={() => setSummaryLanguage(code)}
                      disabled={summaryBusy}
                      aria-pressed={selectedSummaryLanguage === code}
                      className={`rounded-md px-2.5 py-1.5 transition-colors disabled:cursor-wait ${
                        selectedSummaryLanguage === code
                          ? 'bg-surface text-primary-700 shadow-sm'
                          : 'text-muted hover:text-gray-700'
                      }`}
                    >
                      {original ? `${code.toUpperCase()} (original)` : code.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Spec #76 §3.4 — the entity branch reports here as a one-line
              strip only while a task is running or failed. Spec #28
              "failure isolation": independent of the user chain. */}
          <EntityBranchProgress
            episodeId={episode?.id ?? null}
            tasks={episodeTasks}
            hideWhenComplete
            className="mx-4 mt-3 sm:mx-6"
          />

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
                  isLoading={summaryBusy}
                  available={summaryData?.available}
                  episodeState={episode?.state}
                  citations={summaryData?.citations ?? null}
                  onCite={handleSummaryCitation}
                />
              ) : (
                <TranscriptPanel
                  transcriptData={transcriptData}
                  transcriptLoading={transcriptLoading}
                  episodeState={episode?.state}
                  episodeId={episode?.id ?? null}
                  audioUrl={episode?.audio_url ?? null}
                  onSegmentSeek={handleSegmentSeek}
                  entitiesById={entitiesById}
                  mentionsBySegmentId={mentionsBySegmentId}
                  visibleSegmentIds={visibleSegmentIds}
                  scrollToSegment={citationScrollTarget}
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
        </Panel>

        {/* Right rail — only on lg+; collapses below the breakpoint
            (the strip carries the gist on mobile). Always rendered on
            lg+ so the grid's rail column is never a blank gutter and the
            reading column keeps one width across episodes and across
            the related-episodes fetch; the rail supplies its own
            empty-state copy when there is nothing to list. */}
        <div className="hidden lg:block">
          <div className="sticky top-4 space-y-4 rounded-lg border border-hairline bg-surface p-4">
            <EntityRail
              entities={entities}
              onSeek={handleSegmentSeek}
              onFocusEntity={setFocusedEntityId}
              relatedEpisodes={relatedEpisodes}
              relatedLoading={relatedLoading}
              extractionPending={extractionPending}
            />
          </div>
        </div>
      </div>

      {/* Spec #76 §3.5–3.6 — people as content, then every remaining fact
          in one labelled place. Both sit below the tabs so late-arriving
          transcript data cannot move the fold. */}
      {episode && (
        <People entities={entities} segments={transcriptSegments} onSpeakerSelect={handleSpeakerSelect} />
      )}
      {episode && (
        <Panel className="px-4 py-3 sm:px-6 sm:py-4">
          <DefinitionList heading="Information" rows={buildEpisodeInformationRows(episode)} />
        </Panel>
      )}

    </div>
  )
}

/**
 * Transcript panel — renders the segmented viewer (spec #18 Phase D)
 * when a segmented sidecar exists. Episodes without one (mid-pipeline,
 * or still loading) fall back to the plain TranscriptViewer, which also
 * renders the loading / unavailable states.
 */
interface TranscriptPanelProps {
  transcriptData: import('../api/types').ContentResponse | undefined
  transcriptLoading: boolean
  episodeState: string | undefined
  episodeId: string | null
  audioUrl: string | null
  onSegmentSeek: (seconds: number) => void
  // Spec #28 §5.2 — episode-page entity UX. All optional so a viewer
  // mounted without entity data (legacy episodes, tests) keeps working.
  entitiesById?: Map<string, EpisodeEntity>
  mentionsBySegmentId?: Map<number, MentionLite[]>
  visibleSegmentIds?: Set<number> | null
  scrollToSegment?: SegmentScrollTarget | null
  focusedEntityId?: string | null
  onFocusEntity?: (entityId: string) => void
  // Slot for the filter bar — rendered above the segmented viewer so
  // it shares the panel's padding and lives under the sub-tab toggle.
  entityFilterBar?: React.ReactNode
  // Spec #38 karaoke wipe — threaded through from EpisodeReader which
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
  entitiesById,
  mentionsBySegmentId,
  visibleSegmentIds,
  scrollToSegment,
  focusedEntityId,
  onFocusEntity,
  entityFilterBar,
  karaokeEnabled,
  karaokeWords,
  karaokeChipChecked,
  karaokeChipDisabled,
  onKaraokeToggle,
}: TranscriptPanelProps) {
  const liveAudioDuration = useAudioDuration(audioUrl)
  const drift = classifyDrift(
    transcriptData?.segments?.transcript_source_duration_s,
    liveAudioDuration,
  )

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
      {transcriptData?.segments ? (
        <>
          {entityFilterBar && <div className="mb-3">{entityFilterBar}</div>}
          <SegmentedTranscriptViewer
            transcript={transcriptData.segments}
            episodeId={episodeId}
            onSeekRequest={onSegmentSeek}
            entitiesById={entitiesById}
            mentionsBySegmentId={mentionsBySegmentId}
            visibleSegmentIds={visibleSegmentIds}
            scrollToSegmentId={scrollToSegment}
            focusedEntityId={focusedEntityId}
            onFocusEntity={onFocusEntity}
            karaokeEnabled={karaokeEnabled}
            karaokeWords={karaokeWords}
            karaokeChipChecked={karaokeChipChecked}
            karaokeChipDisabled={karaokeChipDisabled}
            onKaraokeToggle={onKaraokeToggle}
          />
        </>
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
