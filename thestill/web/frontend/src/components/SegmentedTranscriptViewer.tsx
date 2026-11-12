import {
  useId,
  Fragment,
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react'
import { abovePlayer } from '../constants/layers'
import { useFollowPlayback } from '../hooks/useFollowPlayback'
import type {
  AnnotatedSegment,
  AnnotatedTranscriptDump,
  EpisodeEntity,
  KaraokeWordsByEpisode,
  MentionLite,
  SegmentKind,
  WordTimestamp,
} from '../api/types'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import type { BreakState } from '../contexts/playback-engine/timeline-map'
import {
  useAutoScrollFollow,
  usePersistedBoolean,
} from '../hooks/useAutoScrollFollow'
import { buildTimestampDeepLink, settleDeepLinkCue, useDeepLinkSeek } from '../hooks/useDeepLinkSeek'
import { deepLinkSegmentIndex } from '../hooks/useDeepLinkScrollTarget'
import { useIsSmUp } from '../hooks/useMediaQuery'
import EntityHighlight from './episode-entities/EntityHighlight'
import { buildSpeakerColorMap, resolveSpeakerColor } from '../utils/speakerColors'
import { HIGHLIGHT_LEAD_SECONDS } from '../utils/highlightLead'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'
import { useKaraokeActiveWordIdx } from '../hooks/useKaraokeActiveWordIdx'
import KaraokeWord from './KaraokeWord'
import { useToast } from './Toast'
import {
  applyEntityHighlights,
  findSpeakerMention,
  highlightMatches,
  type SegmentMentionSet,
} from './episode-entities/applyHighlights'
import { track as trackEvent } from '../analytics/client'

const SHOW_FILLER_STORAGE_KEY = 'thestill:transcript:showFiller'
// Global preference, not per-episode: matches reader intent ("I never
// want to see ads" carries across episodes).
const HIDDEN_KINDS_STORAGE_KEY = 'thestill:transcriptViewer:hiddenKinds'

type TogglableKind = Exclude<SegmentKind, 'filler' | 'content'>

const TOGGLE_ORDER: TogglableKind[] = ['ad_break', 'music', 'intro', 'outro']
// Kinds with a pill in the toggle bar. Ads have none (spec #116 §6.1): an
// ad break is always one collapsed row, opened per row, so a stored
// `ad_break` entry is read and ignored.
const PILL_ORDER: TogglableKind[] = ['music', 'intro', 'outro']

// Sentinel for ContentSegment's karaoke rAF hook when karaoke is off
// for this segment. The hook short-circuits on null words, so this is
// never read — it's just a stable identity so the effect's deps array
// doesn't churn between renders.
const KARAOKE_NOOP_TIME = (): number => 0
const NOOP_REF = (): void => {}

const KIND_LABELS: Record<TogglableKind, string> = {
  ad_break: 'Ads',
  music: 'Music',
  intro: 'Intro',
  outro: 'Outro',
}

interface BlockStyle {
  border: string
  bg: string
  bgActive: string
  text: string
  accent: string
  hoverRing: string
}

const BLOCK_STYLES: Record<TogglableKind, BlockStyle> = {
  ad_break: {
    border: 'border-amber-400',
    bg: 'bg-amber-50/70',
    bgActive: 'bg-amber-100/80 shadow-sm',
    text: 'text-amber-800',
    accent: 'text-amber-700/80',
    hoverRing:
      'hover:bg-amber-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400',
  },
  music: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
  intro: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
  outro: {
    border: 'border-slate-300',
    bg: 'bg-slate-50/70',
    bgActive: 'bg-slate-100/80 shadow-sm',
    text: 'text-slate-700',
    accent: 'text-slate-500',
    hoverRing:
      'hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400',
  },
}

function loadHiddenKinds(): Set<TogglableKind> {
  if (typeof window === 'undefined') return new Set()
  try {
    const raw = window.localStorage.getItem(HIDDEN_KINDS_STORAGE_KEY)
    if (!raw) return new Set()
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(
      parsed.filter((value): value is TogglableKind =>
        typeof value === 'string' && (TOGGLE_ORDER as string[]).includes(value),
      ),
    )
  } catch {
    return new Set()
  }
}

function saveHiddenKinds(kinds: Set<TogglableKind>): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(
      HIDDEN_KINDS_STORAGE_KEY,
      JSON.stringify(Array.from(kinds)),
    )
  } catch {
    // localStorage may be disabled (private mode, quota exceeded) — the
    // preference silently reverts to the session default.
  }
}

interface SegmentedTranscriptViewerProps {
  transcript: AnnotatedTranscriptDump
  // Enables active-segment highlighting when the player is on this episode.
  episodeId?: string | null
  // Called with an absolute playback second when the user clicks or
  // keyboard-activates a segment. Parent decides whether to seek() an
  // already-playing track or play() a new one.
  onSeekRequest?: (seconds: number) => void
  // Spec #28 §5.2 — episode-page entity UX. All optional; the viewer
  // works without entity data (legacy / pre-extraction episodes).
  // Passed in from EpisodeDetail rather than fetched here so the rail
  // and the viewer share one fetch result.
  entitiesById?: Map<string, EpisodeEntity>
  mentionsBySegmentId?: Map<number, MentionLite[]>
  entityHighlightsEnabled?: boolean
  // When non-null, only segments whose id appears in the set are
  // rendered. Spec §5.2 inline filter bar — pure client-side filter.
  visibleSegmentIds?: Set<number> | null
  // `reveal`: the jump is to something inside the segment (a mention, a
  // citation), so an ad break holding it opens. A link to a moment does not
  // set it and lands on the collapsed row.
  scrollToSegmentId?: { segmentId: number; nonce: number; reveal?: boolean } | null
  // The router's `location.key`: a link to a moment (`?t=` / `#t=`) acts
  // once per history entry, so Back to the entry does not seek again.
  locationKey?: string
  // The entity the user last focused (hovered/clicked). Drives the
  // `[`/`]` keyboard nav (spec §5.2 affordance #1).
  focusedEntityId?: string | null
  onFocusEntity?: (entityId: string) => void
  // Spec #38 — karaoke wipe.
  //
  // ``karaokeEnabled`` is the *effective* state (chip is on AND words are
  // loaded for this episode); it drives whether the active segment swaps
  // to KaraokeWord spans. ``karaokeWords`` is the pre-indexed map
  // returned by ``useEpisodeTranscriptWords``.
  //
  // The remaining three props control the toolbar chip itself. The chip
  // is rendered only when ``onKaraokeToggle`` is provided, mirroring the
  // pattern other optional features use. ``karaokeChipChecked`` is the
  // chip's visual state (persisted in the parent); ``karaokeChipDisabled``
  // shows the chip dimmed with a tooltip when the endpoint returned 404.
  karaokeEnabled?: boolean
  karaokeWords?: KaraokeWordsByEpisode | null
  karaokeChipChecked?: boolean
  karaokeChipDisabled?: boolean
  onKaraokeToggle?: () => void
}

function formatTimestamp(seconds: number): string {
  const total = Math.floor(seconds)
  const hh = Math.floor(total / 3600)
  const mm = Math.floor((total % 3600) / 60)
  const ss = total % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  return hh > 0 ? `${pad(hh)}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`
}

function handleActivationKey(event: KeyboardEvent<HTMLDivElement>, onActivate: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    onActivate()
  }
}

type SeekableProps = Pick<
  React.HTMLAttributes<HTMLDivElement>,
  'role' | 'tabIndex' | 'aria-label' | 'onClick' | 'onKeyDown'
>

function seekableProps(
  label: string,
  onSeek: ((seconds: number) => void) | undefined,
  seconds: number,
): SeekableProps {
  if (!onSeek) return {}
  const activate = () => onSeek(seconds)
  return {
    role: 'button',
    tabIndex: 0,
    'aria-label': label,
    onClick: activate,
    onKeyDown: (e) => handleActivationKey(e, activate),
  }
}

interface TimestampLinkProps {
  seconds: number
  className: string
  onCopy: (seconds: number) => void
}

function TimestampLink({ seconds, className, onCopy }: TimestampLinkProps) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onCopy(seconds)
      }}
      title="Copy link to this moment"
      className={`${className} hover:text-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded`}
    >
      {formatTimestamp(seconds)}
    </button>
  )
}

interface BlockSegmentProps {
  segment: AnnotatedSegment
  kind: TogglableKind
  offset: number
  isActive: boolean
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  // When true, collapse to a compact "[00:02] AD BREAK — NAVAN" chip.
  // When false, render the full cleaned ad body with a subtle coloured rail.
  collapsed: boolean
  segmentMentions: SegmentMentionSet | null
  entityHighlightsEnabled: boolean
  onFocusEntity?: (entityId: string) => void
}

const BlockSegment = memo(function BlockSegment({
  segment,
  kind,
  offset,
  isActive,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  collapsed,
  segmentMentions,
  entityHighlightsEnabled,
  onFocusEntity,
}: BlockSegmentProps) {
  const style = BLOCK_STYLES[kind]
  const baseLabel = kind === 'ad_break' ? 'Ad break' : KIND_LABELS[kind]
  const sponsorSuffix =
    kind === 'ad_break' && segment.sponsor ? ` — ${segment.sponsor}` : ''
  const absoluteSeconds = segment.start + offset
  const dimClass = dimmed ? 'opacity-70' : ''
  const interactive = seekableProps(
    `Seek to ${formatTimestamp(absoluteSeconds)} — ${baseLabel}${sponsorSuffix}`,
    onSeek,
    absoluteSeconds,
  )
  const seekableClasses = onSeek ? `cursor-pointer ${style.hoverRing}` : ''

  if (collapsed) {
    const activeRing = isActive ? style.bgActive : ''
    return (
      <div
        {...interactive}
        ref={registerRef}
        data-testid={`segment-${kind}-${segment.id}`}
        data-active={isActive ? 'true' : 'false'}
        data-collapsed="true"
        className={`my-3 border-l-4 ${style.border} ${style.bg} px-4 py-2 rounded-r-md transition-shadow ${activeRing} ${dimClass} ${seekableClasses}`}
      >
        <div className={`flex items-center gap-3 ${style.text}`}>
          <TimestampLink
            seconds={absoluteSeconds}
            onCopy={onCopyTimestamp}
            className={`font-mono text-[11px] tabular-nums ${style.accent}`}
          />
          <span className="text-xs font-semibold uppercase tracking-wider">
            {baseLabel}
            {highlightMatches(sponsorSuffix, searchQuery)}
          </span>
        </div>
      </div>
    )
  }

  // Expanded: full ad copy reads like body text but keeps the kind's
  // accent colour on the left rail so the reader still clocks it as
  // sponsored content. No label chip — the user reads the ad body directly.
  const containerActive = isActive ? style.bgActive : 'hover:bg-gray-50/70'
  return (
    <div
      {...interactive}
      ref={registerRef}
      data-testid={`segment-${kind}-${segment.id}`}
      data-active={isActive ? 'true' : 'false'}
      data-collapsed="false"
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${dimClass} ${seekableClasses}`}
    >
      <p
        className={`text-gray-800 pl-4 border-l-2 ${style.border} text-base leading-[1.7] !mb-0`}
      >
        <TimestampLink
          seconds={absoluteSeconds}
          onCopy={onCopyTimestamp}
          className={`mr-2 font-mono text-[11px] tabular-nums align-baseline ${style.accent}`}
        />
        {segment.text
          ? applyEntityHighlights({
              text: segment.text,
              segmentMentions,
              enabled: entityHighlightsEnabled,
              searchQuery,
              onSeek,
              onFocusEntity,
            })
          : (
            <span className="text-gray-400 italic">
              (empty {baseLabel.toLowerCase()})
            </span>
          )}
      </p>
    </div>
  )
})

interface ContentSegmentProps {
  segment: AnnotatedSegment
  offset: number
  isActive: boolean
  // Resolved speaker colour (assigned by order of appearance upstream), used
  // for both the label text and the active-segment left-accent border.
  speakerColor: string
  onSeek?: (seconds: number) => void
  registerRef?: (el: HTMLElement | null) => void
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  isFiller: boolean
  // The passage a link to a moment landed on, until it is played.
  linked?: boolean
  segmentMentions: SegmentMentionSet | null
  entityHighlightsEnabled: boolean
  onFocusEntity?: (entityId: string) => void
  // Spec #38 karaoke wipe. Present only on the active segment when
  // karaoke is on AND the episode has word data for this segment.
  // When set, the cleaned-text body is replaced by a flow of
  // ``KaraokeWord`` spans (raw words, raw spacing). The viewer is
  // "honest about what we know" — the cleaned wording is set aside for
  // the active segment so the highlight tracks the actual audio.
  // The active-word index is computed *inside* this component via a
  // per-frame rAF loop, not derived from the 4 Hz ``usePlayerTime`` —
  // the difference is the gap between "the gradient inside one word is
  // smooth" and "the highlight handoff between words is also smooth".
  karaokeWords?: WordTimestamp[]
  karaokeGetCurrentTime?: () => number
}

const ContentSegment = memo(function ContentSegment({
  segment,
  offset,
  isActive,
  speakerColor,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  isFiller,
  linked = false,
  segmentMentions,
  entityHighlightsEnabled,
  onFocusEntity,
  karaokeWords,
  karaokeGetCurrentTime,
}: ContentSegmentProps) {
  // Per-frame karaoke cursor: which word is currently spoken (-1 during
  // pauses) and how far through the segment the audio has progressed.
  // The "read-up-to" cutoff is what makes the highlight stable through
  // pauses — without it, every gap between words flips the segment back
  // to grey. The hook returns { -1, -1 } for inactive segments, so the
  // call is safe on every render.
  const { activeIdx: karaokeActiveWordIdx, readUpTo: karaokeReadUpTo } =
    useKaraokeActiveWordIdx(
      karaokeWords ?? null,
      offset,
      karaokeGetCurrentTime ?? KARAOKE_NOOP_TIME,
    )
  const speaker = segment.speaker ?? 'Unknown'
  // The speaker label is an entity link when the extractor linked this
  // segment's speaker to a person: same peek as an in-text mention, with
  // the host/guest badge — the one place a host who is never *named* in
  // the transcript gets one.
  const speakerMention = entityHighlightsEnabled ? findSpeakerMention(segmentMentions) : null
  const absoluteSeconds = segment.start + offset
  const containerActive = isActive
    ? 'bg-primary-50/70'
    : linked
      ? 'bg-primary-50/50 ring-1 ring-inset ring-primary-200'
      : 'hover:bg-gray-50/70'
  const paragraphAccent = 'border-l-2'
  const timestampColor = isActive ? 'text-primary-700' : 'text-gray-400'
  const dimClass = dimmed ? 'opacity-70' : ''
  const bodyClass = isFiller ? 'text-gray-500 italic' : 'text-gray-800'
  const interactive = seekableProps(
    `Seek to ${formatTimestamp(absoluteSeconds)} — ${speaker}`,
    onSeek,
    absoluteSeconds,
  )
  const seekableClasses = onSeek
    ? 'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400'
    : ''
  return (
    <div
      {...interactive}
      ref={registerRef}
      data-testid={`segment-content-${segment.id}`}
      data-active={isActive ? 'true' : 'false'}
      data-filler={isFiller ? 'true' : 'false'}
      data-linked={linked ? 'true' : undefined}
      className={`group -mx-2 px-2 py-2.5 rounded-lg transition-colors sm:-mx-3 sm:px-3 ${containerActive} ${dimClass} ${seekableClasses}`}
    >
      <p
        className={`${bodyClass} pl-4 ${paragraphAccent} border-gray-200 text-base leading-[1.7] !mb-0`}
        style={{ borderLeftColor: isActive ? speakerColor : undefined }}
      >
        <TimestampLink
          seconds={absoluteSeconds}
          onCopy={onCopyTimestamp}
          className={`mr-2 font-mono text-[11px] tabular-nums align-baseline ${timestampColor}`}
        />
        <span
          className="font-sans font-semibold tracking-tight"
          style={{ color: speakerColor }}
        >
          {speakerMention ? (
            <EntityHighlight
              variant="speaker"
              episodeEntity={speakerMention.entity}
              mention={speakerMention.mention}
              episodeId={segmentMentions?.episodeId ?? null}
              isSmUp={segmentMentions?.isSmUp ?? true}
              onSeek={onSeek}
              onFocusEntity={onFocusEntity}
            >
              {speaker}
            </EntityHighlight>
          ) : (
            speaker
          )}
          {isFiller && (
            <span className="ml-1.5 font-mono text-[10px] font-normal uppercase tracking-wider text-gray-400">
              filler
            </span>
          )}
          :
        </span>{' '}
        {karaokeWords && karaokeGetCurrentTime
          ? karaokeWords.map((w, i) => (
              <Fragment key={i}>
                <KaraokeWord
                  word={w}
                  read={i <= karaokeReadUpTo}
                  isActive={i === karaokeActiveWordIdx}
                />
                {i < karaokeWords.length - 1 ? ' ' : ''}
              </Fragment>
            ))
          : applyEntityHighlights({
              text: segment.text,
              segmentMentions,
              enabled: entityHighlightsEnabled,
              searchQuery,
              onSeek,
              onFocusEntity,
            })}
      </p>
    </div>
  )
})

interface LinkedMomentBarProps {
  seconds: number
  onPlay: () => void
  // True until the button has taken focus once for this offer.
  focusOnArrival: boolean
  onFocused: () => void
}

/**
 * Sits on the passage a shared link (`#t=` / `?t=`) landed on. The link was
 * opened cold, so nothing is playing: this button is the visitor's own tap,
 * which is what lets the audio start in every browser.
 */
function LinkedMomentBar({ seconds, onPlay, focusOnArrival, onFocused }: LinkedMomentBarProps) {
  const noteId = useId()
  // The page scrolled here by itself; bring the keyboard along, or the
  // button is hundreds of Tab stops away. No scroll: the jump owns that.
  // Once per offer: the bar mounts again when a search or filter moves it
  // to another passage, and must not pull focus out of the search field.
  const takeFocus = useCallback(
    (el: HTMLButtonElement | null) => {
      if (!el || !focusOnArrival) return
      el.focus({ preventScroll: true })
      onFocused()
    },
    [focusOnArrival, onFocused],
  )
  return (
    <div data-testid="linked-moment" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-3 pb-1">
      <button
        ref={takeFocus}
        type="button"
        onClick={onPlay}
        aria-describedby={noteId}
        className="inline-flex items-center gap-1.5 rounded-full bg-primary-900 px-3.5 py-1.5 text-sm font-medium text-white hover:bg-primary-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 focus-visible:ring-offset-2"
      >
        <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M8 5v14l11-7z" />
        </svg>
        Play from {formatTimestamp(seconds)}
      </button>
      <span id={noteId} className="text-xs text-gray-500">
        The moment this link points to
      </span>
    </div>
  )
}

interface ToggleBarProps {
  presentKinds: TogglableKind[]
  hiddenKinds: Set<TogglableKind>
  onToggle: (kind: TogglableKind) => void
}

function ToggleBar({ presentKinds, hiddenKinds, onToggle }: ToggleBarProps) {
  if (presentKinds.length === 0) return null
  return (
    <div
      role="group"
      aria-label="Segment kind filters"
      className="inline-flex flex-wrap items-center gap-1.5"
    >
      <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">Show</span>
      {presentKinds.map((kind) => {
        const isVisible = !hiddenKinds.has(kind)
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={isVisible}
            onClick={() => onToggle(kind)}
            className={
              'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ' +
              (isVisible
                ? 'bg-blue-100 text-blue-800 border border-blue-300'
                : 'bg-gray-100 text-gray-500 border border-gray-200 line-through')
            }
          >
            {KIND_LABELS[kind]}
          </button>
        )
      })}
    </div>
  )
}

// Spec #116 §6.1 — an ad break: a maximal run of consecutive `ad_break`
// segments (filler inside a run does not split it). It is one row at all
// times; a timeline map changes the row's text, never its height.
interface AdRun {
  /** The first segment's id: the row's key and scroll target. */
  id: number
  memberIds: ReadonlySet<number>
  /** Playback seconds (segment time + the transcript's offset). */
  startS: number
  endS: number
  /** Distinct sponsors, in order. */
  sponsors: string[]
}

function buildAdRuns(segments: ReadonlyArray<AnnotatedSegment>, offset: number): AdRun[] {
  const runs: AdRun[] = []
  let i = 0
  while (i < segments.length) {
    if (segments[i].kind !== 'ad_break') {
      i += 1
      continue
    }
    let last = i
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segments[j].kind === 'ad_break') last = j
      else if (segments[j].kind !== 'filler') break
    }
    const members = segments.slice(i, last + 1)
    const sponsors: string[] = []
    for (const seg of members) {
      if (seg.sponsor && !sponsors.includes(seg.sponsor)) sponsors.push(seg.sponsor)
    }
    runs.push({
      id: members[0].id,
      memberIds: new Set(members.map((seg) => seg.id)),
      startS: members[0].start + offset,
      endS: members[members.length - 1].end + offset,
      sponsors,
    })
    i = last + 1
  }
  return runs
}

type AdRowState = 'plain' | BreakState['kind']

function formatLength(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  return `${Math.floor(total / 60)}:${(total % 60).toString().padStart(2, '0')}`
}

const MAX_SPONSORS_SHOWN = 3

/** The row's text after "Ad break": what the listener's copy has there. */
function adRowDetail(state: AdRowState, run: AdRun, listenerAdS: number | null): string {
  if (state === 'absent') return 'not in your copy'
  if (state === 'different') {
    return listenerAdS === null ? 'different in your copy' : `different in your copy · ${formatLength(listenerAdS)}`
  }
  // The sponsors named in the transcribed copy still hold.
  const shown = run.sponsors.slice(0, MAX_SPONSORS_SHOWN).join(', ')
  const more = run.sponsors.length > MAX_SPONSORS_SHOWN ? ` +${run.sponsors.length - MAX_SPONSORS_SHOWN}` : ''
  const parts = [shown + more, formatLength(run.endS - run.startS)].filter(Boolean)
  if (state === 'unresolved') parts.push('timing approximate')
  return parts.join(' · ')
}

/** "0:23 left" for the ad of the listener's own that is playing. The
 * player clock stands still during it, so this reads the element. */
function AdCountdown({ endS, getAssetTime }: { endS: number; getAssetTime: () => number }) {
  const [left, setLeft] = useState(() => Math.max(0, Math.ceil(endS - getAssetTime())))
  useEffect(() => {
    const timer = window.setInterval(() => setLeft(Math.max(0, Math.ceil(endS - getAssetTime()))), 500)
    return () => window.clearInterval(timer)
  }, [endS, getAssetTime])
  return <> · {formatLength(left)} left</>
}

interface AdBreakRowProps {
  run: AdRun
  state: AdRowState
  /** Length of the listener's own ad there (state `different`). */
  listenerAdS: number | null
  /** An ad of the listener's own is playing at this break; when it ends
   * (seconds on the listener's copy), or null if that is not known. */
  adPlaying: boolean
  adEndS: number | null
  getAssetTime: () => number
  isActive: boolean
  open: boolean
  onToggle: (run: AdRun, state: AdRowState, wasOpen: boolean) => void
  onSeek?: (seconds: number) => void
  registerRef: (el: HTMLElement | null) => void
  onCopyTimestamp: (seconds: number) => void
  searchQuery: string
  dimmed: boolean
  /** The break's segments, when open. */
  children?: ReactNode
}

const AdBreakRow = memo(function AdBreakRow({
  run,
  state,
  listenerAdS,
  adPlaying,
  adEndS,
  getAssetTime,
  isActive,
  open,
  onToggle,
  onSeek,
  registerRef,
  onCopyTimestamp,
  searchQuery,
  dimmed,
  children,
}: AdBreakRowProps) {
  const style = BLOCK_STYLES.ad_break
  const panelId = useId()
  const detail = adRowDetail(state, run, listenerAdS)
  const title = adPlaying ? 'Ad playing' : 'Ad break'
  const replaced = state === 'different' || state === 'absent'
  // A break that is gone from this copy has nothing to seek to.
  const interactive = seekableProps(
    `Seek to ${formatTimestamp(run.startS)} — Ad break`,
    replaced ? undefined : onSeek,
    run.startS,
  )
  const seekableClasses = interactive.onClick ? `cursor-pointer ${style.hoverRing}` : ''
  return (
    <div
      {...interactive}
      ref={registerRef}
      data-testid={`ad-break-${run.id}`}
      data-state={adPlaying ? 'playing' : state}
      data-active={isActive ? 'true' : 'false'}
      data-open={open ? 'true' : 'false'}
      className={`my-3 border-l-4 ${style.border} ${isActive ? style.bgActive : style.bg} rounded-r-md transition-shadow ${dimmed ? 'opacity-70' : ''} ${seekableClasses}`}
    >
      {/* One line, fixed height: the text is cut, never wrapped, so the
          row is the same height in every state and at every width. */}
      <div className={`flex h-9 items-center gap-3 px-4 ${style.text}`}>
        <TimestampLink
          seconds={run.startS}
          onCopy={onCopyTimestamp}
          className={`shrink-0 font-mono text-[11px] tabular-nums ${style.accent}`}
        />
        <span data-testid={`ad-break-text-${run.id}`} className="min-w-0 flex-1 truncate text-xs">
          <span className="font-semibold uppercase tracking-wider">{title}</span>
          {adPlaying ? (
            adEndS !== null && <AdCountdown endS={adEndS} getAssetTime={getAssetTime} />
          ) : (
            <> · {highlightMatches(detail, searchQuery)}</>
          )}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`${open ? 'Hide' : 'Show'} ad text. ${title}, ${detail}`}
          onClick={(e) => {
            e.stopPropagation()
            onToggle(run, state, open)
          }}
          onKeyDown={(e) => e.stopPropagation()}
          className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 text-xs font-medium ${style.accent} hover:text-amber-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400`}
        >
          <span className="hidden sm:inline">{open ? 'Hide ad text' : 'Show ad text'}</span>
          <svg
            className={`h-3.5 w-3.5 transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>
      {open && (
        // The segments seek for themselves; a click on one must not also
        // reach the row and seek to the break's start.
        <div id={panelId} className="px-4 pb-3" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <p className={`mb-1 text-xs ${style.accent}`}>
            From the copy we transcribed.{replaced ? ' Your copy plays other ads here.' : ''}
          </p>
          {children}
        </div>
      )}
    </div>
  )
})

// Rows are a normal segment render, an ad break (one row for its whole run
// of segments), or a placeholder for a run of non-matching segments
// collapsed under a single "N hidden" pill.
type RenderRow =
  | { type: 'segment'; segment: AnnotatedSegment; expandedFromHidden: boolean }
  | { type: 'adBreak'; run: AdRun; members: AnnotatedSegment[]; expandedFromHidden: boolean; matched: boolean }
  | { type: 'hidden'; firstId: number; count: number }

/** The id a row scrolls and is cued by, and where it starts. */
function rowAnchor(row: Exclude<RenderRow, { type: 'hidden' }>): { id: number; start: number } {
  return row.type === 'adBreak'
    ? { id: row.run.id, start: row.members[0].start }
    : { id: row.segment.id, start: row.segment.start }
}

// Spec #28 §5.2 affordance #2 — `E` toggle persistence key.
const SHOW_ENTITY_HIGHLIGHTS_STORAGE_KEY = 'thestill:transcript:showEntityHighlights'

interface ActiveSegmentTrackerProps {
  segments: AnnotatedSegment[]
  offset: number
  renderedIdSet: Set<number>
  onActiveChange: (id: number | null) => void
}

/**
 * Spec #69 Phase 7.2 — the only subscriber to the 4Hz player clock.
 *
 * Renders nothing; on each tick it resolves the active segment (binary
 * search over the full list so filler gaps and hidden kinds don't flicker
 * the highlight, then a walk back to the nearest *visible* segment so the
 * outline lands on something the reader can see) and reports upward only
 * when the id actually changes. The 4Hz re-render is confined to this
 * null-rendering leaf; the ~10k-row viewer re-renders per segment
 * transition instead of per tick. Exported for tests.
 */
export function ActiveSegmentTracker({
  segments,
  offset,
  renderedIdSet,
  onActiveChange,
}: ActiveSegmentTrackerProps) {
  const currentTime = usePlayerTime()
  const activeId = useMemo(() => {
    // The lead keeps the segment highlight in step with the karaoke
    // read-word colouring, and lets a new segment's first word lead too
    // (words only render once their segment is active).
    const idx = findActiveSegmentIndex(
      segments,
      currentTime + HIGHLIGHT_LEAD_SECONDS,
      offset,
    )
    if (idx < 0) return null
    for (let i = idx; i >= 0; i -= 1) {
      if (renderedIdSet.has(segments[i].id)) {
        return segments[i].id
      }
    }
    return null
  }, [segments, currentTime, offset, renderedIdSet])
  useEffect(() => {
    onActiveChange(activeId)
  }, [activeId, onActiveChange])
  return null
}

export default function SegmentedTranscriptViewer({
  transcript,
  episodeId,
  onSeekRequest,
  entitiesById,
  mentionsBySegmentId,
  entityHighlightsEnabled: entityHighlightsEnabledProp,
  visibleSegmentIds,
  scrollToSegmentId,
  locationKey,
  focusedEntityId: focusedEntityIdProp,
  onFocusEntity: onFocusEntityProp,
  karaokeEnabled,
  karaokeWords,
  karaokeChipChecked,
  karaokeChipDisabled,
  onKaraokeToggle,
}: SegmentedTranscriptViewerProps) {
  const offset = transcript.playback_time_offset_seconds ?? 0
  // Assign speaker colours by order of appearance so each distinct (resolved)
  // speaker gets a stable, maximally separated colour. Merged diarisation
  // labels share one entry → one colour.
  //
  // Only `content`/`filler` segments render with a speaker colour (everything
  // else is a BlockSegment, below). Restrict the map to those kinds so a
  // speaker preserved on an `ad_break`/`intro`/etc. row can't consume a
  // palette slot — that would shift every later speaker's colour and make the
  // same person read with a different colour from one episode to the next.
  const speakerColorMap = useMemo(
    () =>
      buildSpeakerColorMap(
        transcript.segments
          .filter((s) => s.kind === 'content' || s.kind === 'filler')
          .map((s) => s.speaker),
      ),
    [transcript.segments],
  )
  // Spec #72 §6 — shared with the Now Playing sheet's toggle.
  const [followPlayback, setFollowPlayback] = useFollowPlayback()
  const [showFiller, setShowFiller] = usePersistedBoolean(SHOW_FILLER_STORAGE_KEY, false)
  const [hiddenKinds, setHiddenKinds] = useState<Set<TogglableKind>>(loadHiddenKinds)
  const [searchInput, setSearchInput] = useState('')
  const searchQuery = useDeferredValue(searchInput.trim())
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0)
  const [expandedHiddenGroups, setExpandedHiddenGroups] = useState<Set<number>>(
    () => new Set(),
  )
  // Affordance #2: `E` toggle to show/hide entity highlights. The
  // parent can override the default-on behavior by passing
  // `entityHighlightsEnabled` explicitly; otherwise we fall back to
  // the persisted user pref (default on).
  const [entityHighlightsLocal, setEntityHighlightsLocal] = usePersistedBoolean(
    SHOW_ENTITY_HIGHLIGHTS_STORAGE_KEY,
    true,
  )
  // The keydown handler reads through this ref so a held / rapid `E`
  // press doesn't see a stale closure value between renders.
  const entityHighlightsLocalRef = useRef(entityHighlightsLocal)
  useEffect(() => {
    entityHighlightsLocalRef.current = entityHighlightsLocal
  }, [entityHighlightsLocal])
  const entityHighlightsEnabled =
    entityHighlightsEnabledProp ?? entityHighlightsLocal
  // The focused entity is also kept locally if no parent state is
  // wired in. Useful for embedding the viewer in contexts that don't
  // care about coordinating with a strip / rail.
  const [focusedEntityIdLocal, setFocusedEntityIdLocal] = useState<string | null>(null)
  const focusedEntityId = focusedEntityIdProp ?? focusedEntityIdLocal
  const onFocusEntity = useCallback(
    (entityId: string) => {
      if (onFocusEntityProp) onFocusEntityProp(entityId)
      else setFocusedEntityIdLocal(entityId)
    },
    [onFocusEntityProp],
  )
  const { showToast } = useToast()

  const toggleKind = useCallback((kind: TogglableKind) => {
    setHiddenKinds((prev) => {
      const next = new Set(prev)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      saveHiddenKinds(next)
      return next
    })
  }, [])

  // Togglable kinds are always in the rendered list — toggling the pill
  // flips them between compact chip and full-text; it never drops them.
  // Spec §5.2 inline filter bar: when `visibleSegmentIds` is non-null,
  // also drop any segment whose id isn't in the set. This is applied
  // on top of the existing kind/filler filters so the user can stack
  // both ("show only segments mentioning X, with ads hidden").
  const { presentKinds, renderedSegments } = useMemo(() => {
    const present = new Set<TogglableKind>()
    const visible: AnnotatedSegment[] = []
    for (const seg of transcript.segments) {
      if (seg.kind === 'filler') {
        if (showFiller) visible.push(seg)
        continue
      }
      if (seg.kind !== 'content') {
        present.add(seg.kind as TogglableKind)
      }
      visible.push(seg)
    }
    const filtered = visibleSegmentIds
      ? visible.filter((s) => visibleSegmentIds.has(s.id))
      : visible
    return {
      presentKinds: PILL_ORDER.filter((kind) => present.has(kind)),
      renderedSegments: filtered,
    }
  }, [transcript.segments, showFiller, visibleSegmentIds])

  const { track, getCurrentTime, isPlaying, isLoading, timelineMap, listenerAd, nativeDuration, getAssetTime } =
    usePlayer()
  const isCurrentEpisode = !!episodeId && track?.episodeId === episodeId

  // Spec #116 §6.1 — ad breaks, each one row. Built from every segment,
  // not the rendered ones, so a break is the same row whatever is hidden.
  const adRuns = useMemo(() => buildAdRuns(transcript.segments, offset), [transcript.segments, offset])
  const adRunBySegmentId = useMemo(() => {
    const out = new Map<number, AdRun>()
    for (const run of adRuns) for (const id of run.memberIds) out.set(id, run)
    return out
  }, [adRuns])
  // What the listener's copy has at each break, once a map is in force for
  // this episode's audio. Without one every row is plain.
  const breakMap = isCurrentEpisode ? timelineMap : null
  const breakStates = useMemo(() => {
    if (!breakMap) return null
    const out = new Map<number, BreakState>()
    for (const run of adRuns) out.set(run.id, breakMap.classifyBreak(run.startS, run.endS, nativeDuration))
    return out
  }, [breakMap, adRuns, nativeDuration])
  // The break an ad of the listener's own is playing at: the one between
  // the same two spans. None when the ad is only in the listener's copy.
  const playingAd = isCurrentEpisode ? listenerAd : null
  const playingAdRunId = useMemo(() => {
    if (!playingAd) return null
    const run = adRuns.find((r) => r.startS <= playingAd.srcToS + 1 && r.endS >= playingAd.srcFromS - 1)
    return run?.id ?? null
  }, [playingAd, adRuns])
  // Rows the reader opened. Search, an entity filter and a jump to
  // something inside a break open a row too, without being recorded here.
  const [openAdRuns, setOpenAdRuns] = useState<Set<number>>(() => new Set())
  const toggleAdRun = useCallback((run: AdRun, state: AdRowState, wasOpen: boolean) => {
    setOpenAdRuns((prev) => {
      const next = new Set(prev)
      if (wasOpen) next.delete(run.id)
      else next.add(run.id)
      return next
    })
    // Whether anyone reads ad text decides if it stays (spec §6.1).
    if (!wasOpen) trackEvent('ad_break_expanded', { state })
  }, [])
  const revealAdRun = useCallback(
    (segmentId: number) => {
      const run = adRunBySegmentId.get(segmentId)
      if (run) setOpenAdRuns((prev) => (prev.has(run.id) ? prev : new Set(prev).add(run.id)))
    },
    [adRunBySegmentId],
  )
  // A jump to something inside a segment opens the break that holds it.
  // Adjusted during render, once per jump (no extra commit).
  const [revealedNonce, setRevealedNonce] = useState<number | null>(null)
  if (scrollToSegmentId?.reveal && scrollToSegmentId.nonce !== revealedNonce) {
    setRevealedNonce(scrollToSegmentId.nonce)
    revealAdRun(scrollToSegmentId.segmentId)
  }

  // Spec #69 Phase 7.2 — the 4Hz player clock is confined to the
  // ActiveSegmentTracker leaf below. The viewer itself re-renders only
  // when the *active segment* changes (every few seconds of audio), not
  // on every time tick: previously the whole ~10k-row tree re-rendered
  // (and rebuilt a Set over every rendered segment) four times a second
  // for the entire playback session. When another episode is playing the
  // tracker is unmounted and the last tracked id is masked at render
  // time (no reset effect — a setState-in-effect would cascade renders).
  const [trackedSegmentId, setTrackedSegmentId] = useState<number | null>(null)
  const activeSegmentId = isCurrentEpisode ? trackedSegmentId : null

  // Computed once per filter change — not per tick (it used to live
  // inside the per-tick active-segment memo).
  const renderedIdSet = useMemo(
    () => new Set(renderedSegments.map((s) => s.id)),
    [renderedSegments],
  )

  // Spec #38 — karaoke wipe. Resolve the words for the active segment.
  // Inactive segments stay on cleaned text so DOM cost stays
  // O(words-in-active-segment), not O(all-words). The active-word index
  // itself is computed inside ContentSegment via a per-frame rAF loop
  // (``useKaraokeActiveWordIdx``) so word transitions land within ~16 ms
  // of the audio cursor — `usePlayerTime` only ticks at 4 Hz and can
  // miss short words entirely.
  const activeKaraoke = useMemo(() => {
    if (!karaokeEnabled) return null
    if (!karaokeWords || activeSegmentId === null) return null
    const wordsForSegment = karaokeWords.wordsBySegmentId.get(activeSegmentId)
    if (!wordsForSegment || wordsForSegment.length === 0) return null
    return {
      segmentId: activeSegmentId,
      words: wordsForSegment,
    }
  }, [karaokeEnabled, karaokeWords, activeSegmentId])

  const follow = useAutoScrollFollow({
    // During an ad of the listener's own, the break's row is what plays.
    activeKey: playingAdRunId ?? activeSegmentId,
    enabled: followPlayback && isCurrentEpisode,
  })

  useEffect(() => {
    if (!scrollToSegmentId) return
    let key = scrollToSegmentId.segmentId
    if (!renderedIdSet.has(key)) {
      // The target has no row (hidden filler, an entity filter): go to the
      // next passage that has one — where a linked moment's "Play from"
      // sits too — or the last one.
      const target = transcript.segments.find((s) => s.id === key)
      const next = target
        ? (renderedSegments.find((s) => s.start >= target.start) ?? renderedSegments[renderedSegments.length - 1])
        : undefined
      if (!next) return
      key = next.id
    }
    follow.scrollToKey(key)
    // Fires per requested jump, not when the row set changes; follow is
    // stable from useAutoScrollFollow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollToSegmentId?.segmentId, scrollToSegmentId?.nonce])

  const handleCopyTimestamp = useCallback(
    async (seconds: number) => {
      const url = buildTimestampDeepLink(seconds)
      try {
        await navigator.clipboard.writeText(url)
        trackEvent('share', { kind: 'moment' })
        showToast(`Copied link at ${formatTimestamp(seconds)}`, 'success')
      } catch {
        showToast('Failed to copy link', 'error')
      }
    },
    [showToast],
  )

  // A link to a moment marks its passage and offers "Play from". Opened cold
  // that is all: nothing sounds until asked. Clicked inside the app it also
  // plays at once, and the offer is the fallback if the browser refuses.
  // Kept with its episode and history entry, so it cannot show on another
  // episode or on another entry of this one (Back/Forward keep the viewer
  // mounted).
  const [cue, setCue] = useState<{ entry: string; seconds: number; focus: boolean } | null>(null)
  const cueEpisodeId = episodeId ?? null
  const cueEntry = `${cueEpisodeId}|${locationKey ?? ''}`
  useDeepLinkSeek(
    cueEpisodeId,
    useCallback(
      (seconds: number) => onSeekRequest?.(seconds),
      [onSeekRequest],
    ),
    !!onSeekRequest && transcript.segments.length > 0,
    useCallback((seconds: number) => setCue({ entry: cueEntry, seconds, focus: true }), [cueEntry]),
    locationKey,
  )
  const cuedSeconds = cue !== null && cue.entry === cueEntry ? cue.seconds : null
  const cueNeedsFocus = cue?.focus === true
  const markCueFocused = useCallback(() => setCue((c) => (c && c.focus ? { ...c, focus: false } : c)), [])
  const dropCue = useCallback(() => {
    setCue(null)
    settleDeepLinkCue(cueEpisodeId, locationKey)
  }, [cueEpisodeId, locationKey])
  const playCuedMoment = useCallback(() => {
    if (cuedSeconds === null) return
    dropCue()
    onSeekRequest?.(cuedSeconds)
  }, [cuedSeconds, dropCue, onSeekRequest])
  // The episode is audibly playing — from the offer, a paragraph, the hero
  // button, or the in-app play going through — so the offer has served.
  const playbackFlowing = isCurrentEpisode && isPlaying && !isLoading
  if (cuedSeconds !== null && playbackFlowing) setCue(null)
  useEffect(() => {
    if (playbackFlowing) settleDeepLinkCue(cueEpisodeId, locationKey)
  }, [playbackFlowing, cueEpisodeId, locationKey])

  const { matchingIds, matchOrder } = useMemo(() => {
    if (!searchQuery) return { matchingIds: null as Set<number> | null, matchOrder: [] as number[] }
    const needle = searchQuery.toLowerCase()
    const ids = new Set<number>()
    const order: number[] = []
    for (const seg of renderedSegments) {
      const hit =
        seg.text.toLowerCase().includes(needle) ||
        (seg.sponsor?.toLowerCase().includes(needle) ?? false) ||
        (seg.speaker?.toLowerCase().includes(needle) ?? false)
      if (hit) {
        ids.add(seg.id)
        order.push(seg.id)
      }
    }
    return { matchingIds: ids, matchOrder: order }
  }, [searchQuery, renderedSegments])

  // A new search starts from the first match with hidden groups collapsed.
  // Adjusted during render rather than in an effect (no extra commit).
  const [appliedSearchQuery, setAppliedSearchQuery] = useState(searchQuery)
  if (searchQuery !== appliedSearchQuery) {
    setAppliedSearchQuery(searchQuery)
    setCurrentMatchIndex(0)
    setExpandedHiddenGroups(new Set())
  }

  useEffect(() => {
    if (!searchQuery || matchOrder.length === 0) return
    follow.scrollToKey(matchOrder[0])
    // follow is stable from useAutoScrollFollow; intentionally exclude.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, matchOrder])

  const goToMatch = useCallback(
    (delta: number) => {
      if (matchOrder.length === 0) return
      setCurrentMatchIndex((prev) => {
        const next = (prev + delta + matchOrder.length) % matchOrder.length
        follow.scrollToKey(matchOrder[next])
        return next
      })
    },
    [matchOrder, follow],
  )

  const expandHiddenGroup = useCallback((firstId: number) => {
    setExpandedHiddenGroups((prev) => {
      const next = new Set(prev)
      next.add(firstId)
      return next
    })
  }, [])

  // Spec #28 §5.2 — per-segment mention sets, scoped down to entities
  // we have records for. Computed once per (entitiesById x mentions)
  // change rather than per segment render.
  // The peek's desktop/phone form is decided here, once, and carried on
  // every segment's mention set — hundreds of highlights must not each
  // subscribe to the media query.
  const isSmUp = useIsSmUp()
  const segmentMentionSets = useMemo<Map<number, SegmentMentionSet>>(() => {
    const out = new Map<number, SegmentMentionSet>()
    if (!entitiesById || !mentionsBySegmentId) return out
    for (const [segId, mentions] of mentionsBySegmentId) {
      const eligible = mentions.filter((m) => entitiesById.has(m.entity_id))
      if (eligible.length === 0) continue
      out.set(segId, {
        entityById: entitiesById,
        mentions: eligible,
        episodeId: episodeId ?? null,
        isSmUp,
      })
    }
    return out
  }, [entitiesById, mentionsBySegmentId, episodeId, isSmUp])

  // Affordances #1 (`[` / `]` jump prev/next mention of focused entity)
  // and #2 (`E` toggle highlights). Bind globally on the viewer root —
  // we only react when no input is focused, so typing in the search
  // box doesn't fight the bindings.
  useEffect(() => {
    function isTyping(target: EventTarget | null): boolean {
      if (!(target instanceof HTMLElement)) return false
      const tag = target.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (isTyping(e.target)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'e' || e.key === 'E') {
        if (entityHighlightsEnabledProp !== undefined) return
        e.preventDefault()
        setEntityHighlightsLocal(!entityHighlightsLocalRef.current)
        return
      }
      if (e.key === '[' || e.key === ']') {
        if (!focusedEntityId) return
        const direction = e.key === ']' ? 1 : -1
        const targetMentions: { segmentId: number; startMs: number }[] = []
        if (mentionsBySegmentId) {
          for (const [segId, mentions] of mentionsBySegmentId) {
            for (const m of mentions) {
              if (m.entity_id === focusedEntityId) {
                targetMentions.push({ segmentId: segId, startMs: m.start_ms })
              }
            }
          }
        }
        if (targetMentions.length === 0) return
        targetMentions.sort((a, b) => a.startMs - b.startMs)
        // Spec #69 Phase 7.3 — read the clock at press time instead of
        // closing over the 4Hz `currentTime`: with the time in the dep
        // array this listener was torn down and re-registered on every
        // tick for the whole playback session.
        const currentTimeMs = getCurrentTime() * 1000
        let nextIdx: number
        if (direction === 1) {
          nextIdx = targetMentions.findIndex((m) => m.startMs > currentTimeMs)
          if (nextIdx === -1) nextIdx = 0
        } else {
          // Walk backwards to find the most recent mention before now.
          let candidate = -1
          for (let i = targetMentions.length - 1; i >= 0; i -= 1) {
            if (targetMentions[i].startMs < currentTimeMs) {
              candidate = i
              break
            }
          }
          nextIdx = candidate === -1 ? targetMentions.length - 1 : candidate
        }
        e.preventDefault()
        const target = targetMentions[nextIdx]
        if (onSeekRequest) onSeekRequest(target.startMs / 1000 + offset)
        revealAdRun(target.segmentId)
        follow.scrollToKey(target.segmentId)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [
    focusedEntityId,
    mentionsBySegmentId,
    getCurrentTime,
    onSeekRequest,
    offset,
    follow,
    entityHighlightsEnabledProp,
    setEntityHighlightsLocal,
    revealAdRun,
  ])

  const renderRows = useMemo<RenderRow[]>(() => {
    // Units first: a segment by itself, or the rendered segments of one ad
    // break together.
    const units: { segments: AnnotatedSegment[]; run: AdRun | null }[] = []
    for (const segment of renderedSegments) {
      const run = adRunBySegmentId.get(segment.id) ?? null
      const last = units[units.length - 1]
      if (run && last && last.run === run) last.segments.push(segment)
      else units.push({ segments: [segment], run })
    }
    const toRow = (unit: (typeof units)[number], expandedFromHidden: boolean, matched: boolean): RenderRow =>
      unit.run
        ? { type: 'adBreak', run: unit.run, members: unit.segments, expandedFromHidden, matched }
        : { type: 'segment', segment: unit.segments[0], expandedFromHidden }
    if (!matchingIds) return units.map((unit) => toRow(unit, false, false))
    const rows: RenderRow[] = []
    let hidden: typeof units = []
    const flushHidden = () => {
      if (hidden.length === 0) return
      const firstId = hidden[0].segments[0].id
      if (expandedHiddenGroups.has(firstId)) {
        for (const unit of hidden) rows.push(toRow(unit, true, false))
      } else {
        rows.push({ type: 'hidden', firstId, count: hidden.reduce((n, unit) => n + unit.segments.length, 0) })
      }
      hidden = []
    }
    for (const unit of units) {
      // A break with a match anywhere in it is shown, open.
      if (unit.segments.some((segment) => matchingIds.has(segment.id))) {
        flushHidden()
        rows.push(toRow(unit, false, true))
      } else {
        hidden.push(unit)
      }
    }
    flushHidden()
    return rows
  }, [matchingIds, renderedSegments, expandedHiddenGroups, adRunBySegmentId])

  // One ref callback per break row, registering the row under every one of
  // its segments' ids: a scroll, the active segment or a search match on
  // any of them lands on the row.
  const adRowRefs = useMemo(() => {
    const out = new Map<number, (el: HTMLElement | null) => void>()
    for (const row of renderRows) {
      if (row.type !== 'adBreak') continue
      const registers = row.members.map((segment) => follow.registerRef(segment.id))
      out.set(row.run.id, (el) => registers.forEach((register) => register(el)))
    }
    return out
    // follow is stable from useAutoScrollFollow; intentionally exclude.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderRows])

  // The row the cued moment sits on. When its own segment is not on screen
  // (filler hidden, a search or entity filter), the next row that is. A
  // moment before the first segment (intro silence, a playback offset) cues
  // the first passage, still playing from the linked seconds. With nothing
  // at or after it, the last row — unless a search or filter is what hid
  // it: the offer does not belong on an unrelated earlier match.
  const filtered = matchingIds !== null || visibleSegmentIds != null
  const cuedSegmentId = useMemo(() => {
    if (cuedSeconds === null || transcript.segments.length === 0) return null
    const idx = deepLinkSegmentIndex(transcript.segments, cuedSeconds, offset)
    const target = transcript.segments[Math.max(idx, 0)]
    let last: number | null = null
    for (const row of renderRows) {
      if (row.type === 'hidden') continue
      const anchor = rowAnchor(row)
      const holdsTarget = row.type === 'adBreak' ? row.run.memberIds.has(target.id) : row.segment.id === target.id
      if (holdsTarget || anchor.start >= target.start) return anchor.id
      last = anchor.id
    }
    return filtered ? null : last
  }, [cuedSeconds, transcript.segments, offset, renderRows, filtered])

  if (transcript.segments.length === 0) {
    return (
      <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
        <p className="text-gray-600 font-medium">No segments available</p>
        <p className="text-sm text-gray-400 mt-1">
          This transcript has no segments yet.
        </p>
      </div>
    )
  }

  return (
    <div className="relative">
      {isCurrentEpisode && (
        <ActiveSegmentTracker
          segments={transcript.segments}
          offset={offset}
          renderedIdSet={renderedIdSet}
          onActiveChange={setTrackedSegmentId}
        />
      )}
      <div className="sticky top-0 z-10 -mx-4 sm:-mx-6 px-4 sm:px-6 py-2 mb-3 bg-white border-b border-gray-100 flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="relative flex-1 min-w-[180px]">
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search transcript…"
            aria-label="Search transcript"
            className="w-full rounded-md border border-gray-200 bg-white pl-8 pr-3 py-1.5 text-sm placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          />
          <svg
            className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z" />
          </svg>
        </div>
        {searchQuery && (
          <div className="inline-flex items-center gap-1 text-xs text-gray-600">
            <span className="font-mono tabular-nums min-w-[3.5rem] text-right">
              {matchOrder.length === 0
                ? 'No matches'
                : `${currentMatchIndex + 1} / ${matchOrder.length}`}
            </span>
            <button
              type="button"
              onClick={() => goToMatch(-1)}
              disabled={matchOrder.length === 0}
              aria-label="Previous match"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => goToMatch(1)}
              disabled={matchOrder.length === 0}
              aria-label="Next match"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 text-gray-500 hover:bg-gray-50 hover:text-gray-700 disabled:opacity-40 disabled:hover:bg-white disabled:hover:text-gray-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
          </div>
        )}
        <ToggleBar
          presentKinds={presentKinds}
          hiddenKinds={hiddenKinds}
          onToggle={toggleKind}
        />
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={showFiller}
            onChange={(e) => setShowFiller(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Show filler</span>
        </label>
        <label className="inline-flex items-center gap-2 text-xs text-gray-600 select-none cursor-pointer">
          <input
            type="checkbox"
            checked={followPlayback}
            onChange={(e) => setFollowPlayback(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
          />
          <span>Follow playback</span>
        </label>
        {onKaraokeToggle && (
          <label
            className={
              'inline-flex items-center gap-2 text-xs select-none ' +
              (karaokeChipDisabled
                ? 'text-gray-400 cursor-not-allowed'
                : 'text-gray-600 cursor-pointer')
            }
            title={
              karaokeChipDisabled
                ? 'No word timestamps for this episode'
                : undefined
            }
          >
            <input
              type="checkbox"
              checked={karaokeChipChecked ?? false}
              disabled={karaokeChipDisabled ?? false}
              onChange={onKaraokeToggle}
              className="h-3.5 w-3.5 rounded border-gray-300 text-primary-600 focus:ring-primary-500 disabled:opacity-50"
            />
            <span>Karaoke</span>
          </label>
        )}
      </div>

      {renderedSegments.length === 0 ? (
        <div className="text-center py-16 min-h-[300px] border border-dashed border-gray-200 rounded-lg bg-gray-50/50">
          <p className="text-gray-600 font-medium">No content segments visible</p>
          <p className="text-sm text-gray-400 mt-1">
            Every segment in this transcript was marked as filler. Toggle{' '}
            <em>Show filler</em> above to reveal them.
          </p>
        </div>
      ) : (
        <div className="transcript-content leading-relaxed space-y-[3px]">
          {renderRows.map((row) => {
            if (row.type === 'hidden') {
              return (
                <button
                  key={`hidden-${row.firstId}`}
                  type="button"
                  onClick={() => expandHiddenGroup(row.firstId)}
                  className="my-2 w-full inline-flex items-center justify-center gap-2 rounded-md border border-dashed border-gray-200 bg-gray-50/60 py-1.5 px-3 text-xs text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                >
                  <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                  </svg>
                  <span>
                    {row.count} segment{row.count === 1 ? '' : 's'} hidden
                  </span>
                  <span className="opacity-60">· Click to show</span>
                </button>
              )
            }
            if (row.type === 'adBreak') {
              const { run, members } = row
              const adPlaying = playingAdRunId === run.id
              const breakState = breakStates?.get(run.id)
              const state: AdRowState = breakState?.kind ?? 'plain'
              // Open by the reader, by a search match inside it, or by an
              // entity filter that selected one of its segments.
              const open = openAdRuns.has(run.id) || row.matched || visibleSegmentIds != null
              const replaced = state === 'different' || state === 'absent'
              const memberSeek = replaced ? undefined : onSeekRequest
              const isCuedRow = cuedSeconds !== null && run.id === cuedSegmentId
              return (
                <Fragment key={`ad-${run.id}`}>
                  {isCuedRow && (
                    <LinkedMomentBar
                      seconds={cuedSeconds}
                      onPlay={playCuedMoment}
                      focusOnArrival={cueNeedsFocus}
                      onFocused={markCueFocused}
                    />
                  )}
                  <AdBreakRow
                    run={run}
                    state={state}
                    listenerAdS={breakState?.kind === 'different' ? breakState.listenerAdS : null}
                    adPlaying={adPlaying}
                    adEndS={adPlaying && playingAd && Number.isFinite(playingAd.endS) ? playingAd.endS : null}
                    getAssetTime={getAssetTime}
                    isActive={
                      adPlaying ||
                      (playingAdRunId === null && activeSegmentId !== null && run.memberIds.has(activeSegmentId))
                    }
                    open={open}
                    onToggle={toggleAdRun}
                    onSeek={onSeekRequest}
                    registerRef={adRowRefs.get(run.id) ?? NOOP_REF}
                    onCopyTimestamp={handleCopyTimestamp}
                    searchQuery={searchQuery}
                    dimmed={row.expandedFromHidden}
                  >
                    {open
                      ? members.map((member) =>
                          member.kind === 'ad_break' ? (
                            <BlockSegment
                              key={member.id}
                              segment={member}
                              kind="ad_break"
                              offset={offset}
                              isActive={false}
                              onSeek={memberSeek}
                              onCopyTimestamp={handleCopyTimestamp}
                              searchQuery={searchQuery}
                              dimmed={false}
                              collapsed={false}
                              segmentMentions={segmentMentionSets.get(member.id) ?? null}
                              entityHighlightsEnabled={entityHighlightsEnabled}
                              onFocusEntity={onFocusEntity}
                            />
                          ) : (
                            <ContentSegment
                              key={member.id}
                              segment={member}
                              offset={offset}
                              isActive={false}
                              speakerColor={resolveSpeakerColor(member.speaker, speakerColorMap)}
                              onSeek={memberSeek}
                              onCopyTimestamp={handleCopyTimestamp}
                              searchQuery={searchQuery}
                              dimmed={false}
                              isFiller={member.kind === 'filler'}
                              segmentMentions={segmentMentionSets.get(member.id) ?? null}
                              entityHighlightsEnabled={entityHighlightsEnabled}
                              onFocusEntity={onFocusEntity}
                            />
                          ),
                        )
                      : undefined}
                  </AdBreakRow>
                </Fragment>
              )
            }
            const { segment, expandedFromHidden } = row
            // During an ad of the listener's own that has a row, that row
            // is the active one, not the passage the clock is held on.
            const isActive = playingAdRunId === null && segment.id === activeSegmentId
            const isCued = cuedSeconds !== null && segment.id === cuedSegmentId
            const cueBar = isCued ? (
              <LinkedMomentBar
                seconds={cuedSeconds}
                onPlay={playCuedMoment}
                focusOnArrival={cueNeedsFocus}
                onFocused={markCueFocused}
              />
            ) : null
            const segmentMentions = segmentMentionSets.get(segment.id) ?? null
            if (segment.kind === 'content' || segment.kind === 'filler') {
              const isKaraokeSegment =
                activeKaraoke !== null && activeKaraoke.segmentId === segment.id
              return (
                <Fragment key={segment.id}>
                  {cueBar}
                  <ContentSegment
                    segment={segment}
                    offset={offset}
                    isActive={isActive}
                    speakerColor={resolveSpeakerColor(segment.speaker, speakerColorMap)}
                    onSeek={onSeekRequest}
                    registerRef={follow.registerRef(segment.id)}
                    onCopyTimestamp={handleCopyTimestamp}
                    searchQuery={searchQuery}
                    dimmed={expandedFromHidden}
                    isFiller={segment.kind === 'filler'}
                    segmentMentions={segmentMentions}
                    entityHighlightsEnabled={entityHighlightsEnabled}
                    onFocusEntity={onFocusEntity}
                    karaokeWords={isKaraokeSegment ? activeKaraoke.words : undefined}
                    karaokeGetCurrentTime={
                      isKaraokeSegment ? getCurrentTime : undefined
                    }
                    linked={isCued}
                  />
                </Fragment>
              )
            }
            const togglableKind = segment.kind as TogglableKind
            return (
              <Fragment key={segment.id}>
                {cueBar}
                <BlockSegment
                  segment={segment}
                  kind={togglableKind}
                  offset={offset}
                  isActive={isActive}
                  onSeek={onSeekRequest}
                  registerRef={follow.registerRef(segment.id)}
                  onCopyTimestamp={handleCopyTimestamp}
                  searchQuery={searchQuery}
                  dimmed={expandedFromHidden}
                  collapsed={hiddenKinds.has(togglableKind)}
                  segmentMentions={segmentMentions}
                  entityHighlightsEnabled={entityHighlightsEnabled}
                  onFocusEntity={onFocusEntity}
                />
              </Fragment>
            )
          })}
        </div>
      )}

      {followPlayback && follow.userScrolledAway && activeSegmentId != null && (
        <button
          type="button"
          onClick={follow.resume}
          style={{ bottom: abovePlayer('1rem') }}
          className="fixed right-6 z-20 inline-flex items-center gap-2 rounded-full bg-primary-900 px-4 py-2 text-sm font-medium text-white shadow-lg hover:bg-primary-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" />
          </svg>
          Resume follow
        </button>
      )}
    </div>
  )
}
