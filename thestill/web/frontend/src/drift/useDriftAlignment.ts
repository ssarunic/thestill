// Spec #116 Phase 2 — run one alignment per episode and audio URL when
// playback of that episode starts, report the outcome, and keep the result
// for the admin debug strip. It changes nothing about playback: the map is
// not verified against the audio element yet (§4 step 6), so it is not
// applied.
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { reportDriftOutcome } from '../api/client'
import type { AnnotatedTranscriptDump } from '../api/types'
import { usePlayer } from '../contexts/PlayerContext'
import type { DriftWorkerMessage, DriftWorkerRequest } from '../workers/driftAlign.worker'
import type { TranscriptInterval } from './classify'
import { toOutcomeBody } from './report'
import type { DriftProgress, DriftReport } from './run'

export interface DriftAlignmentState {
  status: 'idle' | 'running' | 'done'
  progress: DriftProgress | null
  report: DriftReport | null
}

const IDLE: DriftAlignmentState = { status: 'idle', progress: null, report: null }

// One attempt per episode, track generation and audio URL for the life of
// the page: the full pass downloads the file a second time.
const attempts = new Map<string, DriftAlignmentState>()
const listeners = new Map<string, Set<() => void>>()

function publish(key: string, state: DriftAlignmentState): void {
  attempts.set(key, state)
  listeners.get(key)?.forEach((listener) => listener())
}

/** Test seam: forget every attempt. */
export function resetDriftAttempts(): void {
  attempts.clear()
  listeners.clear()
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

function startAttempt(
  key: string,
  input: DriftWorkerRequest['input'],
  report: (result: DriftReport) => void,
): void {
  publish(key, { status: 'running', progress: null, report: null })
  const worker = new Worker(new URL('../workers/driftAlign.worker.ts', import.meta.url), { type: 'module' })
  const fail = () => {
    worker.terminate()
    publish(key, { status: 'done', progress: null, report: null })
  }
  worker.onerror = fail
  worker.onmessage = (event: MessageEvent<DriftWorkerMessage>) => {
    const message = event.data
    if (message.type === 'progress') {
      publish(key, { status: 'running', progress: message.progress, report: null })
      return
    }
    worker.terminate()
    publish(key, { status: 'done', progress: null, report: message.report })
    report(message.report)
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
  useEffect(() => {
    if (!key || !isPlaying || attempts.has(key)) return
    // A refetched transcript is a new object, but the attempt is keyed on
    // its generation, so it never restarts.
    if (!transcript || !generation || !audioUrl || !podcastSlug || !episodeSlug) return
    if (typeof Worker === 'undefined' || saveData()) return
    startAttempt(
      key,
      {
        trackUrl: `/api/podcasts/${podcastSlug}/episodes/${episodeSlug}/sync-track/${generation}`,
        audioUrl,
        segments: transcriptIntervals(transcript),
      },
      (report) => {
        // Best effort: a lost report costs one data point.
        void reportDriftOutcome(podcastSlug, episodeSlug, toOutcomeBody(report, generation, navigator.userAgent)).catch(
          () => {},
        )
      },
    )
  }, [key, isPlaying, transcript, generation, audioUrl, podcastSlug, episodeSlug])

  return state
}
