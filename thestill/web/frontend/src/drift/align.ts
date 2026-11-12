// Spec #116 — locate a sync track's landmarks in another copy of the
// episode and fit offset spans. A port of `align` in
// thestill/core/sync_track.py; the two are held to the same answers by
// tests/fixtures/sync_track/align_vectors.json. Change them together.

import { adler32, crc32, windowDigest, WINDOW_FRAMES } from './digest'
import { Mp3FrameParser } from './mp3Frames'
import type { SyncTrack } from './syncTrack'

/** A run of this many consecutive landmarks agreeing on one offset is needed
 * before the offset is trusted: single hits false-match on silence and on
 * repeated ads. */
export const MIN_RUN = 5
/** A run may skip at most this many of the track's landmarks in a row. More
 * means the audio between two hits is not the tracked audio (an ad swapped
 * for another of the same length keeps the offset on both sides). */
export const MAX_MISSED_IN_RUN = 2
/** Two offsets are "the same" within this many seconds; anchor times are
 * stored in milliseconds, so rounding alone can differ by one. */
export const OFFSET_TOLERANCE_S = 0.02

/** Source time `[srcStartS, srcEndS]` is found `offsetS` later in the other
 * copy (negative when that copy is shorter up to this point). */
export interface Span {
  srcStartS: number
  srcEndS: number
  offsetS: number
  anchors: number
}

export interface Alignment {
  spans: Span[]
  anchorsTotal: number
  /** Landmarks found anywhere in the other copy. */
  anchorsHit: number
  /** Landmarks that ended up inside a span. */
  anchorsInlier: number
  /** Start of the last window in the other copy; close enough for a report. */
  otherDurationS: number
  /** Bytes of the other copy that were audio frames. */
  otherBytes: number
  trackDurationS: number
  /** Share of the track's duration that falls inside a span. */
  coverage: number
}

type Hit = [srcS: number, otherS: number]

function bisectLeft(sorted: number[], value: number): number {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (sorted[mid] < value) lo = mid + 1
    else hi = mid
  }
  return lo
}

function bisectRight(sorted: number[], value: number): number {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >>> 1
    if (value < sorted[mid]) hi = mid
    else lo = mid + 1
  }
  return lo
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

/**
 * Streaming aligner: `push` the other copy's bytes as they arrive, then
 * `finish`. Only landmark hits are kept, so memory does not grow with the
 * file.
 */
export class Aligner {
  private readonly wanted = new Map<number, number>()
  private readonly anchorTimes: number[]
  private readonly hits: Hit[] = []
  private readonly parser: Mp3FrameParser
  // The last WINDOW_FRAMES frames: start time, CRC-32, Adler-32.
  private readonly recentStart = new Float64Array(WINDOW_FRAMES)
  private readonly recentDigests = new Uint32Array(WINDOW_FRAMES * 2)
  private readonly ordered = new Uint32Array(WINDOW_FRAMES * 2)
  private frames = 0
  private otherDurationS = 0
  private otherBytes = 0

  constructor(
    private readonly track: SyncTrack,
    private readonly minRun = MIN_RUN,
  ) {
    for (const anchor of track.anchors) this.wanted.set(anchor.digest, anchor.timeS)
    this.anchorTimes = [...this.wanted.values()].sort((a, b) => a - b)
    this.parser = new Mp3FrameParser((startS, buf, offset, length) => this.onFrame(startS, buf, offset, length))
  }

  push(chunk: Uint8Array): void {
    this.parser.push(chunk)
  }

  /** Frames seen so far in the other copy. */
  get frameCount(): number {
    return this.frames
  }

  private onFrame(startS: number, buf: Uint8Array, offset: number, length: number): void {
    const slot = this.frames % WINDOW_FRAMES
    this.recentStart[slot] = startS
    this.recentDigests[slot * 2] = crc32(buf, offset, offset + length)
    this.recentDigests[slot * 2 + 1] = adler32(buf, offset, offset + length)
    this.frames += 1
    this.otherBytes += length
    if (this.frames < WINDOW_FRAMES) {
      this.otherDurationS = this.recentStart[0]
      return
    }
    // The window is the run of frames ending at this one; `oldest` is its
    // first frame in the ring.
    const oldest = this.frames % WINDOW_FRAMES
    for (let k = 0; k < WINDOW_FRAMES; k++) {
      const from = (oldest + k) % WINDOW_FRAMES
      this.ordered[k * 2] = this.recentDigests[from * 2]
      this.ordered[k * 2 + 1] = this.recentDigests[from * 2 + 1]
    }
    const windowStartS = this.recentStart[oldest]
    const srcS = this.wanted.get(windowDigest(this.ordered))
    if (srcS !== undefined) this.hits.push([srcS, windowStartS])
    this.otherDurationS = windowStartS
  }

  private unbroken(earlierSrc: number, laterSrc: number): boolean {
    const between = bisectLeft(this.anchorTimes, laterSrc) - bisectRight(this.anchorTimes, earlierSrc)
    return between <= MAX_MISSED_IN_RUN
  }

  finish(): Alignment {
    this.parser.end()

    const runs: Hit[][] = []
    let current: Hit[] = []
    for (const hit of this.hits) {
      const [srcS, otherS] = hit
      if (current.length) {
        const [prevSrc, prevOther] = current[current.length - 1]
        const sameOffset = Math.abs(otherS - srcS - (prevOther - prevSrc)) <= OFFSET_TOLERANCE_S
        if (sameOffset && srcS > prevSrc && this.unbroken(prevSrc, srcS)) {
          current.push(hit)
          continue
        }
        runs.push(current)
      }
      current = [hit]
    }
    if (current.length) runs.push(current)

    // The same audio can sit in the other copy twice (a promo played as a
    // pre-roll and again later), so runs can overlap in source time. Keep
    // the heaviest chain of runs that moves forward in both copies.
    const candidates = runs.filter((run) => run.length >= this.minRun)
    const best: number[] = []
    const prev: number[] = []
    candidates.forEach((run, i) => {
      best.push(run.length)
      prev.push(-1)
      for (let k = 0; k < i; k++) {
        const last = candidates[k][candidates[k].length - 1]
        if (last[0] < run[0][0] && last[1] < run[0][1] && best[k] + run.length > best[i]) {
          best[i] = best[k] + run.length
          prev[i] = k
        }
      }
    })
    const chain: Hit[][] = []
    if (candidates.length) {
      // The first of equal weights, as Python's max() picks.
      let at = 0
      for (let i = 1; i < best.length; i++) if (best[i] > best[at]) at = i
      while (at >= 0) {
        chain.push(candidates[at])
        at = prev[at]
      }
      chain.reverse()
    }

    const spans: Span[] = []
    let inliers = 0
    for (const run of chain) {
      const offset = median(run.map(([s, o]) => o - s))
      inliers += run.length
      const last = spans[spans.length - 1]
      if (
        last &&
        Math.abs(last.offsetS - offset) <= OFFSET_TOLERANCE_S &&
        this.unbroken(last.srcEndS, run[0][0])
      ) {
        spans[spans.length - 1] = {
          srcStartS: last.srcStartS,
          srcEndS: run[run.length - 1][0],
          offsetS: last.offsetS,
          anchors: last.anchors + run.length,
        }
      } else {
        spans.push({ srcStartS: run[0][0], srcEndS: run[run.length - 1][0], offsetS: offset, anchors: run.length })
      }
    }

    const trackDurationS = this.track.durationS
    const covered = spans.reduce((sum, span) => sum + (span.srcEndS - span.srcStartS), 0)
    return {
      spans,
      anchorsTotal: this.track.anchors.length,
      anchorsHit: this.hits.length,
      anchorsInlier: inliers,
      otherDurationS: this.otherDurationS,
      otherBytes: this.otherBytes,
      trackDurationS,
      coverage: trackDurationS > 0 ? Math.min(1, covered / trackDurationS) : 0,
    }
  }
}

/** Align a whole buffer in one call (tests, and small files). */
export function align(track: SyncTrack, chunks: Iterable<Uint8Array>, minRun = MIN_RUN): Alignment {
  const aligner = new Aligner(track, minRun)
  for (const chunk of chunks) aligner.push(chunk)
  return aligner.finish()
}
