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
})
