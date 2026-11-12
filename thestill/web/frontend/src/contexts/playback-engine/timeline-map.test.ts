import { describe, expect, it } from 'vitest'
import { TimelineMap } from './timeline-map'

// The transcribed copy: 0–20 s episode, a 6 s ad (20–26), 26–46 s episode.
// The listener's copy: an 8 s pre-roll, the first part, a 15 s ad, the rest.
// So the first part plays 8 s later and the second 17 s later.
const spans = [
  { srcStartS: 0, srcEndS: 20, offsetS: 8 },
  { srcStartS: 26, srcEndS: 46, offsetS: 17 },
]
const adInOurCopy = { srcStartS: 20, srcEndS: 26, kind: 'confirmed' as const }

describe('TimelineMap', () => {
  it('maps a time inside a span with that span’s offset, both ways', () => {
    const map = new TimelineMap(spans, [adInOurCopy])
    expect(map.toAsset(10)).toEqual({ time: 18, approximate: false })
    expect(map.toAsset(30)).toEqual({ time: 47, approximate: false })
    expect(map.toLogical(18)).toBe(10)
    expect(map.toLogical(47)).toBe(30)
  })

  it('sends a time inside an ad of the transcribed copy to the start of the next span', () => {
    const map = new TimelineMap(spans, [adInOurCopy])
    expect(map.toAsset(23)).toEqual({ time: 43, approximate: false })
  })

  it('returns null for audio that only the listener’s copy has', () => {
    const map = new TimelineMap(spans, [adInOurCopy])
    expect(map.toLogical(3)).toBeNull() // pre-roll
    expect(map.toLogical(30)).toBeNull() // mid-roll, would fall in our ad
    expect(map.toLogical(41)).toBeNull() // mid-roll, past where the next span starts
  })

  it('carries the preceding offset through an unresolved stretch and flags it', () => {
    const map = new TimelineMap(spans) // nothing says what 20–26 is
    expect(map.toAsset(23)).toEqual({ time: 31, approximate: true })
    expect(map.toLogical(31)).toBe(23)
    // …but not past the point where the next span begins in the transcript.
    expect(map.toLogical(35)).toBeNull()
  })

  it('joins unplaced audio to the span on its own side of an absent stretch', () => {
    // 20–22 is content the aligner missed after the first span, 22–24 an ad
    // only the transcribed copy has, 24–26 content before the second span.
    const map = new TimelineMap(spans, [
      { srcStartS: 20, srcEndS: 22, kind: 'unresolved' },
      { srcStartS: 22, srcEndS: 24, kind: 'confirmed' },
      { srcStartS: 24, srcEndS: 26, kind: 'unresolved' },
    ])
    expect(map.toAsset(21)).toEqual({ time: 29, approximate: true })
    expect(map.toAsset(25)).toEqual({ time: 42, approximate: true })
    expect(map.toLogical(29)).toBe(21)
    expect(map.toLogical(42)).toBe(25)
    expect(map.toLogical(35)).toBeNull() // the listener's own mid-roll
  })

  it('uses the nearest span outside the mapped range', () => {
    const map = new TimelineMap([{ srcStartS: 5, srcEndS: 20, offsetS: 8 }])
    expect(map.toAsset(2)).toEqual({ time: 10, approximate: true })
    expect(map.toAsset(25)).toEqual({ time: 33, approximate: true })
    expect(map.toLogical(10)).toBe(2)
    expect(map.toLogical(33)).toBe(25)
  })

  it('ends at the last span when the transcribed copy closes on an ad', () => {
    const map = new TimelineMap(
      [{ srcStartS: 0, srcEndS: 20, offsetS: 0 }],
      [{ srcStartS: 20, srcEndS: 30, kind: 'confirmed' }],
    )
    expect(map.toAsset(25)).toEqual({ time: 20, approximate: false })
  })

  it('handles a copy that is shorter than the transcribed one', () => {
    const map = new TimelineMap(
      [
        { srcStartS: 0, srcEndS: 15, offsetS: 0 },
        { srcStartS: 25, srcEndS: 40, offsetS: -10 },
      ],
      [{ srcStartS: 15, srcEndS: 25, kind: 'confirmed' }],
    )
    expect(map.toAsset(30)).toEqual({ time: 20, approximate: false })
    expect(map.toLogical(20)).toBe(30)
    expect(map.toAsset(18)).toEqual({ time: 15, approximate: false })
  })

  it('accepts spans in any order', () => {
    const map = new TimelineMap([...spans].reverse(), [adInOurCopy])
    expect(map.toAsset(10).time).toBe(18)
  })

  it('with no spans leaves times alone and says they are approximate', () => {
    const map = new TimelineMap([])
    expect(map.toAsset(12)).toEqual({ time: 12, approximate: true })
    expect(map.toLogical(12)).toBe(12)
    expect(map.isIdentity).toBe(false)
  })

  it('constant() is the single offset of spec #61', () => {
    const map = TimelineMap.constant(3)
    expect(map.toAsset(100)).toEqual({ time: 103, approximate: false })
    expect(map.toLogical(103)).toBe(100)
    expect(map.isIdentity).toBe(false)
    expect(TimelineMap.constant().isIdentity).toBe(true)
  })

  it('toLogicalHeld waits at the point an ad interrupted', () => {
    const map = new TimelineMap(spans, [adInOurCopy])
    expect(map.toLogicalHeld(3)).toBe(0) // pre-roll: the start of the episode
    expect(map.toLogicalHeld(18)).toBe(10)
    expect(map.toLogicalHeld(35)).toBe(20) // mid-roll: where the first part ended
    expect(map.toLogicalHeld(47)).toBe(30)
    expect(new TimelineMap([]).toLogicalHeld(12)).toBe(12)
  })
})

describe('TimelineMap: ads (spec §6.1)', () => {
  const withGap = new TimelineMap(spans, [adInOurCopy])
  const noGap = new TimelineMap(spans)
  // The transcribed copy has a 30 s pre-roll and a 10 s post-roll that the
  // listener's copy lacks; the listener's has a 12 s post-roll of its own.
  const edges = new TimelineMap(
    [{ srcStartS: 30, srcEndS: 90, offsetS: -30 }],
    [
      { srcStartS: 0, srcEndS: 30, kind: 'confirmed' },
      { srcStartS: 90, srcEndS: 100, kind: 'confirmed' },
    ],
  )
  // An ad that was simply cut: nothing took its place.
  const cut = new TimelineMap(
    [
      { srcStartS: 0, srcEndS: 20, offsetS: 0 },
      { srcStartS: 26, srcEndS: 46, offsetS: -6 },
    ],
    [adInOurCopy],
  )

  it('finds the ad of the listener’s own that is playing, with its bounds', () => {
    expect(withGap.listenerAdAt(3)).toEqual({ startS: 0, endS: 8, srcFromS: 0, srcToS: 0 })
    expect(withGap.listenerAdAt(30)).toEqual({ startS: 28, endS: 43, srcFromS: 20, srcToS: 26 })
    expect(withGap.listenerAdAt(18)).toBeNull() // inside a span
    expect(withGap.listenerAdAt(50)).toBeNull()
    expect(edges.listenerAdAt(70)).toEqual({ startS: 60, endS: Infinity, srcFromS: 90, srcToS: Infinity })
    expect(cut.listenerAdAt(20)).toBeNull()
  })

  it('agrees with toLogical: an ad is playing exactly where no transcript time answers', () => {
    for (const map of [withGap, noGap, edges, cut]) {
      for (let a = 0; a <= 80; a += 0.25) {
        const ad = map.listenerAdAt(a)
        if (ad !== null) expect(map.toLogical(a), `a=${a}`).toBeNull()
        // The reverse holds away from a seam shorter than an ad.
        else if (map.toLogical(a) === null) {
          expect(map.listenerAdAt(a - 1) === null || map.listenerAdAt(a + 1) === null, `a=${a}`).toBe(true)
        }
      }
    }
  })

  it('classifies a break of the transcribed copy', () => {
    // Replaced by a longer ad of the listener's own.
    expect(withGap.classifyBreak(20, 26)).toEqual({ kind: 'different', listenerAdS: 15 })
    // Lies inside one span: it plays as transcribed.
    expect(withGap.classifyBreak(5, 9)).toEqual({ kind: 'same' })
    // Cut, with nothing in its place.
    expect(cut.classifyBreak(20, 26)).toEqual({ kind: 'absent' })
    // The aligner could not tell what 20–26 is.
    expect(noGap.classifyBreak(20, 26)).toEqual({ kind: 'unresolved' })
  })

  it('treats a break that is only partly the same as different, counting what the copy plays there', () => {
    // Two ads, 16–20 kept and 20–26 replaced by 15 s: 4 s + 15 s.
    expect(withGap.classifyBreak(16, 26)).toEqual({ kind: 'different', listenerAdS: 19 })
    // 16–20 kept and 20–26 cut: the kept ad still plays, so not "absent".
    expect(cut.classifyBreak(16, 26)).toEqual({ kind: 'different', listenerAdS: 4 })
  })

  it('is the same across two spans that meet, and different when the copy adds an ad between them', () => {
    const meet = new TimelineMap([
      { srcStartS: 0, srcEndS: 23, offsetS: 2 },
      { srcStartS: 23, srcEndS: 60, offsetS: 2 },
    ])
    expect(meet.classifyBreak(20, 26)).toEqual({ kind: 'same' })
    const inserted = new TimelineMap([
      { srcStartS: 0, srcEndS: 23, offsetS: 0 },
      { srcStartS: 23, srcEndS: 60, offsetS: 12 },
    ])
    expect(inserted.classifyBreak(20, 26)).toEqual({ kind: 'different', listenerAdS: 18 })
    // An ad of the listener's own just past the break does not touch it.
    expect(inserted.classifyBreak(10, 23)).toEqual({ kind: 'same' })
  })

  it('takes a break split by filler (confirmed, unresolved, confirmed) as one replaced break', () => {
    // What the aligner reports for ad, filler, ad: the filler is not an ad.
    const split = new TimelineMap(spans, [
      { srcStartS: 20, srcEndS: 23, kind: 'confirmed' },
      { srcStartS: 23, srcEndS: 24, kind: 'unresolved' },
      { srcStartS: 24, srcEndS: 26, kind: 'confirmed' },
    ])
    expect(split.classifyBreak(20, 26)).toEqual({ kind: 'different', listenerAdS: 15 })
    for (let a = 28.5; a < 43; a += 0.5) expect(split.toLogical(a), `a=${a}`).toBeNull()
  })

  it('handles a pre-roll and a post-roll, where there is one neighbouring span', () => {
    expect(edges.classifyBreak(0, 30)).toEqual({ kind: 'absent' })
    // A post-roll of the listener's own has a length only once the copy's is known.
    expect(edges.classifyBreak(90, 100)).toEqual({ kind: 'different', listenerAdS: null })
    expect(edges.classifyBreak(90, 100, 72)).toEqual({ kind: 'different', listenerAdS: 12 })
    expect(edges.classifyBreak(90, 100, 60.2)).toEqual({ kind: 'absent' })
    // A pre-roll replaced by a shorter one.
    const preRoll = new TimelineMap([{ srcStartS: 30, srcEndS: 90, offsetS: -20 }], [{ srcStartS: 0, srcEndS: 30, kind: 'confirmed' }])
    expect(preRoll.classifyBreak(0, 30)).toEqual({ kind: 'different', listenerAdS: 10 })
  })
})
