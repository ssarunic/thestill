// Spec #116 §4 step 5 — what lies between the spans the aligner found.
//
// Missing landmarks are not evidence of an ad: a run stops a little short
// of every splice, and a stretch the publisher re-encoded matches nothing.
// So each stretch of transcript time outside the spans is cut along the
// transcript's own segments:
//
// - under an `ad_break` segment, or under no segment at all (no words), it
//   is a confirmed gap: content the listener's copy does not have;
// - under any other segment it is unresolved: words the transcript has and
//   the aligner could not place.

import type { MapSpan, SourceGap } from '../contexts/playback-engine/timeline-map'

/** One transcript segment on the transcribed copy's timeline. */
export interface TranscriptInterval {
  startS: number
  endS: number
  isAd: boolean
}

export interface Classification {
  gaps: SourceGap[]
  /** Seconds of the transcribed copy absent from the listener's copy. */
  gapsConfirmedS: number
  /** Seconds of transcript the aligner could not place. */
  unresolvedS: number
  /** Seconds of the listener's copy that are not transcript audio (its ads). */
  listenerAdsS: number
  /** Share of the transcript's non-ad time that falls inside a span. */
  coverage: number
}

// Pieces shorter than this are joined to their neighbour; segment edges
// and anchor times do not line up to the millisecond.
const MIN_PIECE_S = 0.25

function overlap(aStart: number, aEnd: number, bStart: number, bEnd: number): number {
  return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart))
}

export function classifyAlignment(
  spans: readonly MapSpan[],
  trackDurationS: number,
  otherDurationS: number,
  segments: readonly TranscriptInterval[],
): Classification {
  const ordered = [...spans].sort((a, b) => a.srcStartS - b.srcStartS)
  const content = segments.filter((s) => !s.isAd && s.endS > s.startS).sort((a, b) => a.startS - b.startS)

  // Transcript time outside every span.
  const outside: [number, number][] = []
  let cursor = 0
  for (const span of ordered) {
    if (span.srcStartS > cursor) outside.push([cursor, span.srcStartS])
    cursor = Math.max(cursor, span.srcEndS)
  }
  if (trackDurationS > cursor) outside.push([cursor, trackDurationS])

  const gaps: SourceGap[] = []
  const add = (startS: number, endS: number, kind: SourceGap['kind']) => {
    if (endS - startS <= 0) return
    const last = gaps[gaps.length - 1]
    if (last && last.kind === kind && Math.abs(last.srcEndS - startS) < 1e-9) last.srcEndS = endS
    else gaps.push({ srcStartS: startS, srcEndS: endS, kind })
  }
  for (const [startS, endS] of outside) {
    if (segments.length === 0) {
      // No transcript to cut along: nothing can be confirmed.
      add(startS, endS, 'unresolved')
      continue
    }
    const pieces: [number, number, SourceGap['kind']][] = []
    let at = startS
    for (const segment of content) {
      if (segment.endS <= at) continue
      if (segment.startS >= endS) break
      const from = Math.max(at, segment.startS)
      const to = Math.min(endS, segment.endS)
      if (from > at) pieces.push([at, from, 'confirmed'])
      pieces.push([from, to, 'unresolved'])
      at = to
    }
    if (at < endS) pieces.push([at, endS, 'confirmed'])
    // Fold slivers into the piece before them (or after, for the first).
    const kept: [number, number, SourceGap['kind']][] = []
    for (const piece of pieces) {
      const previous = kept[kept.length - 1]
      if (previous && (piece[1] - piece[0] < MIN_PIECE_S || previous[2] === piece[2])) previous[1] = piece[1]
      else kept.push([...piece])
    }
    if (kept.length > 1 && kept[0][1] - kept[0][0] < MIN_PIECE_S) {
      kept[1][0] = kept[0][0]
      kept.shift()
    }
    for (const [from, to, kind] of kept) add(from, to, kind)
  }

  const sum = (kind: SourceGap['kind']) =>
    gaps.filter((g) => g.kind === kind).reduce((n, g) => n + (g.srcEndS - g.srcStartS), 0)
  const gapsConfirmedS = sum('confirmed')
  const unresolvedS = sum('unresolved')
  const spanS = ordered.reduce((n, s) => n + (s.srcEndS - s.srcStartS), 0)

  let contentS = 0
  let coveredS = 0
  for (const segment of content) {
    contentS += segment.endS - segment.startS
    for (const span of ordered) coveredS += overlap(segment.startS, segment.endS, span.srcStartS, span.srcEndS)
  }
  // Without segments, fall back to the share of the whole track.
  const coverage = contentS > 0 ? Math.min(1, coveredS / contentS) : trackDurationS > 0 ? Math.min(1, spanS / trackDurationS) : 0

  return {
    gaps,
    gapsConfirmedS,
    unresolvedS,
    // What is left of the listener's copy once the transcript audio it
    // holds (placed or not) is taken out.
    listenerAdsS: Math.max(0, otherDurationS - spanS - unresolvedS),
    coverage,
  }
}
