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

/**
 * A stretch of the listener's copy that the transcript has no words for: an
 * ad only this copy carries (spec §6.1). Times are on the listener's copy.
 */
export interface ListenerAd {
  startS: number
  /** `Infinity` when it runs past the last span (a post-roll). */
  endS: number
  /** The transcript interval it interrupts: between the end of the span
   * before it and the start of the span after it. */
  srcFromS: number
  srcToS: number
}

/**
 * What became of an ad break of the transcribed copy in the listener's
 * (spec §6.1):
 * - `same`: it plays as transcribed;
 * - `different`: it is gone and the copy has audio of its own there,
 *   `listenerAdS` long (null when the length is not known);
 * - `absent`: it is gone and nothing took its place;
 * - `unresolved`: the aligner could not tell.
 */
export type BreakState =
  | { kind: 'same' }
  | { kind: 'different'; listenerAdS: number | null }
  | { kind: 'absent' }
  | { kind: 'unresolved' }

// Audio of the listener's own shorter than this is a seam between two
// spans, not an ad.
const MIN_LISTENER_AD_S = 1
// Two spans that meet may leave this much of a break uncovered.
const SEAM_S = 0.5

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
    // Unplaced transcript audio keeps the offset of the span it is joined
    // to: the one before it, unless an absent stretch lies between them.
    const cutOff = before !== undefined && after !== undefined && this.confirmedBetween(before.srcEndS, t)
    const neighbour = (cutOff ? after : (before ?? after)) as MapSpan
    return { time: t + neighbour.offsetS, approximate: true }
  }

  private confirmedBetween(fromS: number, toS: number): boolean {
    return this.gaps.some((gap) => gap.kind === 'confirmed' && gap.srcEndS > fromS && gap.srcStartS < toS)
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
    // Between spans the copy holds transcript audio the aligner could not
    // place and audio of its own. Unplaced audio joined to the span before
    // carries that span's offset; joined to the span after, that one's. A
    // time that neither reaches without crossing an absent stretch is the
    // listener's own audio.
    if (before) {
      const t = a - before.offsetS
      const limit = after ? after.srcStartS : Infinity
      if (t < limit && !this.confirmedGapAt(t) && !this.confirmedBetween(before.srcEndS, t)) return t
    }
    if (after) {
      const t = a - after.offsetS
      const floor = before ? before.srcEndS : 0
      if (t >= floor && !this.confirmedGapAt(t) && !this.confirmedBetween(t, after.srcStartS)) return t
    }
    return null
  }

  /**
   * Audio time to transcript time for a clock that must always read
   * something: inside audio that only the listener's copy has (an ad), the
   * transcript time stands still at the point the ad interrupted. This is
   * what the player shows and what karaoke follows, so the highlight holds
   * its last word through an ad.
   */
  toLogicalHeld(a: number): number {
    const t = this.toLogical(a)
    if (t !== null) return t
    let held: number | null = null
    for (const span of this.spans) {
      if (span.srcEndS + span.offsetS < a) held = span.srcEndS
    }
    // Before the first span (a pre-roll): wait at its start.
    return held ?? this.spans[0]?.srcStartS ?? a
  }

  /**
   * The copy's own audio between two neighbouring spans (either may be
   * missing: before the first span, after the last), on the copy's clock.
   * It is what `toLogical` answers null for: past the transcript audio
   * joined to `before`, short of the transcript audio joined to `after`.
   */
  private ownAudioBetween(before: MapSpan | undefined, after: MapSpan | undefined): ListenerAd | null {
    const fromS = before ? before.srcEndS : 0
    const toS = after ? after.srcStartS : Infinity
    // Transcript audio joined to `before` runs until the next span or the
    // first stretch that is absent from this copy.
    let startS = 0
    if (before) {
      let reach = toS
      for (const gap of this.gaps) {
        if (gap.kind === 'confirmed' && gap.srcEndS > before.srcEndS) reach = Math.min(reach, Math.max(gap.srcStartS, before.srcEndS))
      }
      startS = reach + before.offsetS
    }
    // Transcript audio joined to `after` starts at the previous span or
    // past the last absent stretch.
    let endS = Infinity
    if (after) {
      let reach = fromS
      for (const gap of this.gaps) {
        if (gap.kind === 'confirmed' && gap.srcStartS < after.srcStartS && gap.srcEndS > fromS) {
          reach = Math.max(reach, Math.min(gap.srcEndS, after.srcStartS))
        }
      }
      endS = reach + after.offsetS
    }
    if (endS - startS < MIN_LISTENER_AD_S) return null
    return { startS, endS, srcFromS: fromS, srcToS: toS }
  }

  /** The ad of the listener's own that is playing at audio time `a`, if any. */
  listenerAdAt(a: number): ListenerAd | null {
    if (this.spans.length === 0) return null
    let before: MapSpan | undefined
    let after: MapSpan | undefined
    for (const span of this.spans) {
      const start = span.srcStartS + span.offsetS
      const end = span.srcEndS + span.offsetS
      if (start <= a && a <= end) return null
      if (end < a) before = span
      else if (after === undefined) after = span
    }
    const own = this.ownAudioBetween(before, after)
    return own !== null && own.startS <= a && a < own.endS ? own : null
  }

  /**
   * What became of the transcribed copy's ad break `[startS, endS]`
   * (transcript time) in this copy. Any part confirmed absent, or any audio
   * of the copy's own inside the break, makes the whole break different;
   * the row does not say which part. Its length is then what this copy
   * plays there: the parts it kept plus its own audio. `assetDurationS` is
   * the copy's length when known; it gives a post-roll of the listener's
   * own a length.
   */
  classifyBreak(startS: number, endS: number, assetDurationS?: number | null): BreakState {
    const spans = this.spans
    let keptS = 0
    for (const span of spans) keptS += Math.max(0, Math.min(endS, span.srcEndS) - Math.max(startS, span.srcStartS))
    // The copy's own audio at every seam between spans that lies inside the
    // break (before the first span and after the last count as seams).
    let ownS: number | null = 0
    for (let i = 0; i <= spans.length; i += 1) {
      const before = i > 0 ? spans[i - 1] : undefined
      const after = i < spans.length ? spans[i] : undefined
      const seamFromS = before ? before.srcEndS : 0
      const seamToS = after ? after.srcStartS : Infinity
      if (!(seamFromS < endS && seamToS > startS)) continue
      const own = this.ownAudioBetween(before, after)
      if (own === null) continue
      const ownEndS = Number.isFinite(own.endS) ? own.endS : assetDurationS
      if (ownEndS == null || !Number.isFinite(ownEndS)) ownS = null
      else if (ownS !== null && ownEndS - own.startS >= MIN_LISTENER_AD_S) ownS += ownEndS - own.startS
    }
    const cut = this.gaps.some((g) => g.kind === 'confirmed' && g.srcStartS < endS && g.srcEndS > startS)
    if (!cut) {
      // Part of the break lies outside every span and nothing says it is
      // absent: the aligner could not tell.
      if (keptS < endS - startS - SEAM_S) return { kind: 'unresolved' }
      // Covered, as one span or several that meet, with nothing added.
      if (ownS === 0) return { kind: 'same' }
    }
    if (ownS === null) return { kind: 'different', listenerAdS: null }
    const playsS = keptS + ownS
    return playsS >= MIN_LISTENER_AD_S ? { kind: 'different', listenerAdS: playsS } : { kind: 'absent' }
  }
}
