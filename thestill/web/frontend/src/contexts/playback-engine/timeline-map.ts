// Spec #116 §5 — a piecewise timeline. Spec #61 maps transcript ("logical")
// time to an asset's time with one number, `timeline_offset`. When the
// listener's copy carries different ads from the transcribed copy, the
// offset changes at every ad break, so the map is a list of spans, each
// with its own offset.
//
// Nothing in the player uses this yet (Phase 2 wires it in behind a flag).

/** Transcript time `[srcStartS, srcEndS]` plays `offsetS` later in the
 * listener's copy. Spans are in increasing order in both copies. */
export interface MapSpan {
  srcStartS: number
  srcEndS: number
  offsetS: number
}

/**
 * A stretch of transcript time between spans, classified by the aligner
 * (spec §4 step 5):
 * - `confirmed`: content that is absent from the listener's copy, such as
 *   an ad that only the transcribed copy carried;
 * - `unresolved`: the aligner could not tell.
 * Time between spans that no gap covers is treated as unresolved.
 */
export interface SourceGap {
  srcStartS: number
  srcEndS: number
  kind: 'confirmed' | 'unresolved'
}

export interface AssetTime {
  /** Seconds on the listener's copy. */
  time: number
  /** True when the time came from a neighbouring span's offset, not from a
   * span that contains it. The reader shows its drift note on these. */
  approximate: boolean
}

export class TimelineMap {
  readonly spans: readonly MapSpan[]
  readonly gaps: readonly SourceGap[]

  constructor(spans: readonly MapSpan[], gaps: readonly SourceGap[] = []) {
    this.spans = [...spans].sort((a, b) => a.srcStartS - b.srcStartS)
    this.gaps = [...gaps].sort((a, b) => a.srcStartS - b.srcStartS)
  }

  /** One offset for the whole episode: what the player does without a map. */
  static constant(offsetS = 0): TimelineMap {
    return new TimelineMap([{ srcStartS: -Infinity, srcEndS: Infinity, offsetS }])
  }

  /** True when the map moves no timestamp. */
  get isIdentity(): boolean {
    return this.spans.length > 0 && this.spans.every((span) => span.offsetS === 0) && this.gaps.length === 0
  }

  private confirmedGapAt(t: number): SourceGap | undefined {
    return this.gaps.find((gap) => gap.kind === 'confirmed' && gap.srcStartS <= t && t <= gap.srcEndS)
  }

  /**
   * Transcript time to audio time. A time inside a confirmed gap maps to
   * the start of the next span (the content is not in this copy, so the
   * nearest thing after it is). A time in an unresolved stretch maps with
   * the preceding span's offset and is flagged approximate.
   */
  toAsset(t: number): AssetTime {
    const spans = this.spans
    if (spans.length === 0) return { time: t, approximate: true }
    let before: MapSpan | undefined
    let after: MapSpan | undefined
    for (const span of spans) {
      if (span.srcStartS <= t && t <= span.srcEndS) return { time: t + span.offsetS, approximate: false }
      if (span.srcEndS < t) before = span
      else if (after === undefined) after = span
    }
    if (this.confirmedGapAt(t)) {
      if (after) return { time: after.srcStartS + after.offsetS, approximate: false }
      // A confirmed gap after the last span: the copy ends there.
      const last = before as MapSpan
      return { time: last.srcEndS + last.offsetS, approximate: false }
    }
    const neighbour = (before ?? after) as MapSpan
    return { time: t + neighbour.offsetS, approximate: true }
  }

  /**
   * Audio time to transcript time, or null inside audio that only the
   * listener's copy has (an ad). Karaoke holds its last word on null.
   */
  toLogical(a: number): number | null {
    const spans = this.spans
    if (spans.length === 0) return a
    let before: MapSpan | undefined
    let after: MapSpan | undefined
    for (const span of spans) {
      const start = span.srcStartS + span.offsetS
      const end = span.srcEndS + span.offsetS
      if (start <= a && a <= end) return a - span.offsetS
      if (end < a) before = span
      else if (after === undefined) after = span
    }
    // Between spans the copy holds, in some order, transcript audio the
    // aligner could not place and audio of its own. Carry the neighbouring
    // offset across the first; everything else is the listener's own.
    const neighbour = (before ?? after) as MapSpan
    const t = a - neighbour.offsetS
    if (t < 0) return null
    if (after && t >= after.srcStartS) return null
    if (this.confirmedGapAt(t)) return null
    return t
  }
}
