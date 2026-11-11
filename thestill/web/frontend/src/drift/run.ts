// Spec #116 §4 — one attempt to map a transcript onto the copy of the
// audio this browser is served: fetch the sync track, stream the audio
// through the aligner, classify what lies between the spans.
//
// This is the unverified half. Nothing here knows whether the audio
// element is playing the same copy that was fetched (step 6, the service
// worker), so the result is reported and shown to admins, never applied to
// playback.

import { Aligner, OFFSET_TOLERANCE_S } from './align'
import { classifyAlignment, type TranscriptInterval } from './classify'
import { BlockDigester } from './digest'
import { CODEC_MP3, parseSyncTrack, SyncTrackError } from './syncTrack'
import type { SourceGap } from '../contexts/playback-engine/timeline-map'

export type DriftOutcome =
  | 'identity_assumed'
  | 'identity'
  | 'aligned'
  | 'no_cors'
  | 'variant_mismatch'
  | 'low_confidence'
  | 'unsupported_codec'
  | 'error'

/** A map is applied only when spans cover this share of non-ad transcript time. */
export const MIN_COVERAGE = 0.9

export interface DriftInput {
  /** Same-origin URL of the episode's sync track. */
  trackUrl: string
  /** The audio URL the player is playing. */
  audioUrl: string
  /** The transcript's segments on the transcribed copy's timeline. */
  segments: TranscriptInterval[]
}

export interface DriftReport {
  outcome: DriftOutcome
  generation: string | null
  spans: { srcStartS: number; srcEndS: number; offsetS: number }[]
  gaps: SourceGap[]
  gapsConfirmedS: number | null
  unresolvedS: number | null
  listenerAdsS: number | null
  coverage: number | null
  anchorsTotal: number | null
  anchorsHit: number | null
  anchorsInlier: number | null
  /** Host that served the audio after redirects. */
  audioHost: string | null
  /** Byte length of the transcribed copy, from the track. */
  audioBytes: number | null
  fetchedBytes: number | null
  fetchMs: number | null
  fitMs: number | null
  /** A short machine-readable reason, for `error` and `unsupported_codec`. */
  error: string | null
  /**
   * `[crc, adler, …]` per whole 64 KB block of the fetched copy, for the
   * content check (verify.ts). Never sent to the server. Null until the
   * whole file has been read.
   */
  blocks: Uint32Array | null
}

export interface DriftProgress {
  fetchedBytes: number
  /** From Content-Length; null when the host does not say. */
  totalBytes: number | null
}

function emptyReport(outcome: DriftOutcome, error: string | null = null): DriftReport {
  return {
    outcome,
    generation: null,
    spans: [],
    gaps: [],
    gapsConfirmedS: null,
    unresolvedS: null,
    listenerAdsS: null,
    coverage: null,
    anchorsTotal: null,
    anchorsHit: null,
    anchorsInlier: null,
    audioHost: null,
    audioBytes: null,
    fetchedBytes: null,
    fetchMs: null,
    fitMs: null,
    error,
    blocks: null,
  }
}

function hostOf(url: string): string | null {
  try {
    return new URL(url).hostname || null
  } catch {
    return null
  }
}

/**
 * Run one alignment. Never throws: every failure is an outcome. Aborting
 * `signal` rejects with the fetch's AbortError, which the caller ignores.
 */
export async function runDriftAlignment(
  input: DriftInput,
  options: { signal?: AbortSignal; onProgress?: (progress: DriftProgress) => void; now?: () => number } = {},
): Promise<DriftReport> {
  const { signal, onProgress } = options
  const now = options.now ?? (() => performance.now())
  const aborted = () => signal?.aborted === true

  let track
  try {
    const response = await fetch(input.trackUrl, { credentials: 'include', signal })
    if (!response.ok) return emptyReport('error', `track_http_${response.status}`)
    track = parseSyncTrack(await response.arrayBuffer())
  } catch (error) {
    if (aborted()) throw error
    return emptyReport('error', error instanceof SyncTrackError ? 'track_corrupt' : 'track_fetch_failed')
  }

  const report = emptyReport('error')
  report.generation = track.generation
  report.audioBytes = track.audioBytes
  report.anchorsTotal = track.anchors.length
  report.audioHost = hostOf(input.audioUrl)
  if (track.codec !== CODEC_MP3 || track.anchors.length === 0) {
    return { ...report, outcome: 'unsupported_codec', error: 'track_has_no_anchors' }
  }

  const started = now()
  let response: Response
  try {
    // No credentials: the audio host is a third party, and a wildcard
    // Access-Control-Allow-Origin only satisfies an anonymous request.
    response = await fetch(input.audioUrl, { mode: 'cors', credentials: 'omit', signal })
  } catch (error) {
    if (aborted()) throw error
    // A CORS refusal and a network failure are indistinguishable by design.
    return { ...report, outcome: 'no_cors' }
  }
  report.audioHost = hostOf(response.url) ?? report.audioHost
  if (!response.ok || !response.body) {
    return { ...report, outcome: 'error', error: `audio_http_${response.status}` }
  }

  const declared = Number(response.headers.get('content-length'))
  const totalBytes = Number.isFinite(declared) && declared > 0 ? declared : null
  const aligner = new Aligner(track)
  const digester = new BlockDigester()
  let fetchedBytes = 0
  let lastProgress = 0
  try {
    const reader = response.body.getReader()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      fetchedBytes += value.length
      aligner.push(value)
      digester.push(value)
      if (onProgress && fetchedBytes - lastProgress >= 1 << 20) {
        lastProgress = fetchedBytes
        onProgress({ fetchedBytes, totalBytes })
      }
    }
  } catch (error) {
    if (aborted()) throw error
    return { ...report, outcome: 'error', error: 'audio_stream_failed', fetchedBytes, fetchMs: Math.round(now() - started) }
  }
  onProgress?.({ fetchedBytes, totalBytes: totalBytes ?? fetchedBytes })
  report.fetchedBytes = fetchedBytes
  report.fetchMs = Math.round(now() - started)
  report.blocks = digester.finish()

  const fitStarted = now()
  const alignment = aligner.finish()
  if (aligner.frameCount === 0) {
    return { ...report, outcome: 'unsupported_codec', error: 'audio_has_no_mp3_frames' }
  }
  const spans = alignment.spans.map(({ srcStartS, srcEndS, offsetS }) => ({ srcStartS, srcEndS, offsetS }))
  const classified = classifyAlignment(spans, alignment.trackDurationS, alignment.otherDurationS, input.segments)
  report.fitMs = Math.round(now() - fitStarted)
  report.spans = spans
  report.gaps = classified.gaps
  report.gapsConfirmedS = classified.gapsConfirmedS
  report.unresolvedS = classified.unresolvedS
  report.listenerAdsS = classified.listenerAdsS
  report.coverage = classified.coverage
  report.anchorsHit = alignment.anchorsHit
  report.anchorsInlier = alignment.anchorsInlier
  report.error = null

  if (classified.coverage < MIN_COVERAGE) return { ...report, outcome: 'low_confidence' }
  const unshifted = spans.every((span) => Math.abs(span.offsetS) <= OFFSET_TOLERANCE_S)
  return { ...report, outcome: unshifted && fetchedBytes === track.audioBytes ? 'identity' : 'aligned' }
}
