// Spec #116 Phase 2 — run one alignment per episode and audio URL when
// playback of that episode starts, report the outcome, and keep the result
// for the admin debug strip. It changes nothing about playback: the map is
// not verified against the audio element yet (§4 step 6), so it is not
// applied.
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { reportDriftOutcome } from '../api/client'
import type { AnnotatedTranscriptDump } from '../api/types'
import { usePlayer } from '../contexts/PlayerContext'
import type { AppliedTimeline } from '../contexts/playback-engine/native-engine'
import { TimelineMap } from '../contexts/playback-engine/timeline-map'
import type { DriftWorkerMessage, DriftWorkerRequest } from '../workers/driftAlign.worker'
import type { TranscriptInterval } from './classify'
import { currentAudioLoad, setCorsVerdict } from './corsVerdict'
import { toOutcomeBody, type PlayCheck } from './report'
import {
  ensureDriftServiceWorker,
  mediaReportsFor,
  onMediaReport,
  serviceWorkerControlling,
  serviceWorkerSupported,
} from './swClient'
import { verifyAgainst } from './verify'
import type { DriftProgress, DriftReport } from './run'

export interface DriftAlignmentState {
  status: 'idle' | 'running' | 'done'
  progress: DriftProgress | null
  report: DriftReport | null
  /** Whether the audio element was shown to be playing the aligned copy. */
  check: PlayCheck | null
  /** True while the map is in force in the player (spec §5). */
  applied: boolean
}

const IDLE: DriftAlignmentState = { status: 'idle', progress: null, report: null, check: null, applied: false }

// The worker's reports of the element's first responses can trail the
// alignment by a moment; wait this long before judging the play.
const CHECK_SETTLE_MS = 1500

/**
 * Spec #116 §4 step 6 — was the element playing the copy that was aligned?
 * Only answerable when the element loaded with `crossorigin` and the
 * service worker read its responses; every other case says why not.
 */
export function checkPlay(audioUrl: string, report: DriftReport): PlayCheck {
  if (!serviceWorkerSupported()) return { swState: 'unsupported', verification: null }
  const load = currentAudioLoad()
  if (!load || load.url !== audioUrl || !load.crossOrigin) return { swState: 'element_not_cors', verification: null }
  const seen = mediaReportsFor(audioUrl, report.audioHost, load.t)
  if (seen.length === 0) {
    return { swState: serviceWorkerControlling() ? 'no_requests' : 'not_controlling', verification: null }
  }
  if (seen.every((r) => r.opaque)) return { swState: 'opaque', verification: null }
  if (report.fetchedBytes === null || report.blocks === null) return { swState: 'ok', verification: null }
  return {
    swState: 'ok',
    verification: verifyAgainst(seen, { fetchedBytes: report.fetchedBytes, blocks: report.blocks }),
  }
}

// One attempt per episode, track generation and audio URL for the life of
// the page: the full pass downloads the file a second time.
const attempts = new Map<string, DriftAlignmentState>()
const listeners = new Map<string, Set<() => void>>()
// The audio load (its start time) each attempt's check was made for.
const checkedLoads = new Map<string, number>()

function publish(key: string, state: DriftAlignmentState): void {
  attempts.set(key, state)
  listeners.get(key)?.forEach((listener) => listener())
}

/** Test seam: forget every attempt. */
export function resetDriftAttempts(): void {
  attempts.clear()
  listeners.clear()
  checkedLoads.clear()
}

/** The transcript's segments on the transcribed copy's timeline. */
export function transcriptIntervals(transcript: AnnotatedTranscriptDump): TranscriptInterval[] {
  const offset = transcript.playback_time_offset_seconds ?? 0
  return transcript.segments.map((segment) => ({
    startS: segment.start + offset,
    endS: segment.end + offset,
    isAd: segment.kind === 'ad_break',
  }))
}

function saveData(): boolean {
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection
  return connection?.saveData === true
}

type ReportPlay = (result: DriftReport, check: PlayCheck | null, revision: number, applied: boolean) => void

/** Put a map in force in the player, or lift it; false when the player refused. */
type ApplyTimeline = (timeline: AppliedTimeline | null) => boolean

interface PlayHooks {
  report: ReportPlay
  apply: ApplyTimeline
}

/**
 * Spec #116 §4 step 7 — the map goes to the player only when the copy is
 * shifted (`aligned`; an identity needs no map) and both checks of step 6
 * have passed for the audio now playing. Any other verdict lifts it: a map
 * that cannot be tied to the playing copy must not move a single seek.
 */
function settleTimeline(result: DriftReport, check: PlayCheck | null, apply: ApplyTimeline): boolean {
  const verified = check?.verification?.verdict === 'verified'
  if (verified && result.outcome === 'aligned' && result.trackDurationS !== null && result.spans.length > 0) {
    return apply({ map: new TimelineMap(result.spans, result.gaps), durationS: result.trackDurationS })
  }
  apply(null)
  return false
}

/** A play is re-reported at most this many times after its first report. */
const MAX_REVISIONS = 2

const verdictOf = (check: PlayCheck) => check.verification?.verdict ?? 'unverified'

/**
 * Spec #116 §4 step 6 — "the first response and every later range must
 * match". The first check sees only what the element had asked for by
 * then; two copies of equal length can share their opening and differ at a
 * later ad break, which only a seek exposes. So every further response of
 * the same audio load is checked too. The state follows each change, and a
 * changed verdict is reported again: a play found to be another copy after
 * all, or one that could only be verified later.
 */
function keepChecking(
  key: string,
  audioUrl: string,
  result: DriftReport,
  first: PlayCheck,
  { report, apply }: PlayHooks,
): void {
  const load = currentAudioLoad()
  if (!load || load.url !== audioUrl) return
  let reported = verdictOf(first)
  let shown = JSON.stringify(first)
  let revision = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  const stop = onMediaReport(() => {
    if (timer !== null) return
    // Several ranges arrive together after a seek; judge them once.
    timer = setTimeout(() => {
      timer = null
      const now = currentAudioLoad()
      // Another source has been loaded: this play is over.
      if (!now || now.url !== load.url || now.t !== load.t) return stop()
      const check = checkPlay(audioUrl, result)
      const signature = JSON.stringify(check)
      const verdict = verdictOf(check)
      // Whether the map is in force is read from the attempt each time, not
      // remembered here: the hook can apply it later (a map the player
      // refused while the listener was on another rendition).
      let applied = attempts.get(key)?.applied ?? false
      // The map follows the verdict at once: lifted the moment a range does
      // not match, also after it has been applied.
      if (verdict !== reported) applied = settleTimeline(result, check, apply)
      if (signature !== shown) {
        shown = signature
        publish(key, { ...IDLE, status: 'done', report: result, check, applied })
      }
      // A mismatch is final. A verified play can still be found out.
      if (verdict === reported || verdict === 'unverified') return
      reported = verdict
      revision += 1
      report(result, check, revision, applied)
      if (verdict === 'mismatch' || revision >= MAX_REVISIONS) stop()
    }, 500)
  })
}

/**
 * Check the play now in the element against an alignment, report it, and
 * keep checking. Called when an alignment finishes, and again for every
 * later load of the same audio on this page: coming back to an episode
 * loads it afresh, possibly as another copy, and the earlier verdict says
 * nothing about that. The alignment and its block digest are reused; the
 * file is not downloaded again.
 */
function checkThisLoad(
  key: string,
  audioUrl: string,
  result: DriftReport,
  hooks: PlayHooks,
  /** The alignment has not been reported yet: its outcome must reach the
   * server even if the play it belongs to is over before it can be checked. */
  firstReport: boolean,
): void {
  const { report } = hooks
  const load = currentAudioLoad()
  checkedLoads.set(key, load && load.url === audioUrl ? load.t : -1)
  publish(key, { ...IDLE, status: 'done', report: result })
  setTimeout(() => {
    const now = currentAudioLoad()
    // Superseded by a newer load of the same audio, or by another source
    // (the listener moved on within the settle delay). There is nothing
    // left to check this alignment against, and checking it against the
    // replacement would be wrong; the outcome is still reported, with the
    // check unknown.
    if (load && (!now || now.url !== load.url || now.t !== load.t)) {
      if (firstReport) report(result, null, 0, false)
      return
    }
    const check = checkPlay(audioUrl, result)
    const applied = settleTimeline(result, check, hooks.apply)
    publish(key, { ...IDLE, status: 'done', report: result, check, applied })
    report(result, check, 0, applied)
    keepChecking(key, audioUrl, result, check, hooks)
  }, CHECK_SETTLE_MS)
}

function startAttempt(
  key: string,
  input: DriftWorkerRequest['input'],
  hooks: PlayHooks,
): void {
  publish(key, { ...IDLE, status: 'running' })
  const worker = new Worker(new URL('../workers/driftAlign.worker.ts', import.meta.url), { type: 'module' })
  const fail = () => {
    worker.terminate()
    publish(key, { ...IDLE, status: 'done' })
  }
  worker.onerror = fail
  worker.onmessage = (event: MessageEvent<DriftWorkerMessage>) => {
    const message = event.data
    if (message.type === 'progress') {
      publish(key, { ...IDLE, status: 'running', progress: message.progress })
      return
    }
    worker.terminate()
    const result = message.report
    // The alignment's own fetch is the CORS probe: remember what it found
    // for this redirect chain, for the next load of the element.
    if (result.outcome === 'no_cors') setCorsVerdict(input.audioUrl, 'no_cors')
    else if (result.fetchedBytes !== null) setCorsVerdict(input.audioUrl, 'cors_ok')
    checkThisLoad(key, input.audioUrl, result, hooks, true)
  }
  worker.postMessage({ type: 'start', input } satisfies DriftWorkerRequest)
}

export function useDriftAlignment({
  enabled,
  podcastSlug,
  episodeSlug,
  episodeId,
  transcript,
}: {
  enabled: boolean
  podcastSlug: string | undefined
  episodeSlug: string | undefined
  episodeId: string | undefined
  transcript: AnnotatedTranscriptDump | null | undefined
}): DriftAlignmentState {
  const player = usePlayer()
  const generation = transcript?.sync_track_generation ?? null

  // Only the RSS audio on the native engine: a video rendition or the
  // YouTube player is a different recording with its own timeline.
  const track = player.track
  const playingThis = Boolean(episodeId) && track?.episodeId === episodeId
  const asset = track?.playback?.audio ?? null
  const isHls = /mpegurl/i.test(asset?.mime_type ?? '')
  const audioUrl =
    playingThis && player.activeEngine === 'native' && player.activeRendition === 'audio' && !isHls
      ? (asset?.url ?? track?.audioUrl ?? null)
      : null

  const key = enabled && generation && audioUrl && episodeId ? `${episodeId}|${generation}|${audioUrl}` : null
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (!key) return () => {}
      const set = listeners.get(key) ?? new Set()
      listeners.set(key, set)
      set.add(onChange)
      return () => {
        set.delete(onChange)
      }
    },
    [key],
  )
  const state = useSyncExternalStore(subscribe, () => (key ? (attempts.get(key) ?? IDLE) : IDLE))

  const isPlaying = player.isPlaying
  const applyTimeline = player.applyTimeline
  useEffect(() => {
    if (!key || !isPlaying) return
    // A refetched transcript is a new object, but the attempt is keyed on
    // its generation, so it never restarts.
    if (!transcript || !generation || !audioUrl || !podcastSlug || !episodeSlug) return
    const hooks: PlayHooks = {
      report: (result, check, revision, applied) => {
        // Best effort: a lost report costs one data point.
        void reportDriftOutcome(
          podcastSlug,
          episodeSlug,
          toOutcomeBody(result, generation, navigator.userAgent, check, revision, applied),
        ).catch(() => {})
      },
      // The player refuses unless this episode's audio is still what it is
      // playing, so a late verdict can never touch another source.
      apply: (timeline) => (episodeId ? applyTimeline(episodeId, audioUrl, timeline) : false),
    }
    const earlier = attempts.get(key)
    if (earlier) {
      // Aligned before on this page. If the element has since loaded this
      // audio again (the listener went to another episode and came back),
      // that is a new play to check; the alignment itself is reused.
      const load = currentAudioLoad()
      if (earlier.status === 'done' && earlier.report && load?.url === audioUrl && checkedLoads.get(key) !== load.t) {
        checkThisLoad(key, audioUrl, earlier.report, hooks, false)
      } else if (
        earlier.status === 'done' &&
        earlier.report &&
        earlier.check &&
        !earlier.applied &&
        load?.url === audioUrl
      ) {
        // Same load, verified, but the player refused the map when it was
        // offered: the listener was on the YouTube player or the video
        // rendition at that moment. This effect runs again once the audio
        // is what is playing, so offer it again.
        if (settleTimeline(earlier.report, earlier.check, hooks.apply)) {
          publish(key, { ...earlier, applied: true })
          hooks.report(earlier.report, earlier.check, MAX_REVISIONS + 1, true)
        }
      }
      return
    }
    if (typeof Worker === 'undefined' || saveData()) return
    // Registered on the first play of an episode that has a track, never
    // at page load. It takes control of this page without a reload.
    void ensureDriftServiceWorker()
    startAttempt(
      key,
      {
        trackUrl: `/api/podcasts/${podcastSlug}/episodes/${episodeSlug}/sync-track/${generation}`,
        audioUrl,
        segments: transcriptIntervals(transcript),
      },
      hooks,
    )
  }, [key, isPlaying, transcript, generation, audioUrl, podcastSlug, episodeSlug, episodeId, applyTimeline])

  return state
}
