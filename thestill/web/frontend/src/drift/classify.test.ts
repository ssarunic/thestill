import { describe, expect, it } from 'vitest'
import { classifyAlignment } from './classify'

// Today in Focus, as measured: the run before the mid-roll stops 3.6 s
// short of the ad break and the next starts 1.7 s after it.
const spans = [
  { srcStartS: 0, srcEndS: 2623, offsetS: 0 },
  { srcStartS: 2688.7, srcEndS: 3282, offsetS: 40.124 },
]
const segments = [
  { startS: 0, endS: 2626.6, isAd: false },
  { startS: 2626.6, endS: 2687, isAd: true },
  { startS: 2687, endS: 3290, isAd: false },
]

describe('classifyAlignment', () => {
  it('cuts the stretch between two spans along the transcript segments', () => {
    const result = classifyAlignment(spans, 3290, 3335, segments)
    expect(result.gaps).toEqual([
      { srcStartS: 2623, srcEndS: 2626.6, kind: 'unresolved' },
      { srcStartS: 2626.6, srcEndS: 2687, kind: 'confirmed' },
      { srcStartS: 2687, srcEndS: 2688.7, kind: 'unresolved' },
      { srcStartS: 3282, srcEndS: 3290, kind: 'unresolved' },
    ])
    expect(result.gapsConfirmedS).toBeCloseTo(60.4)
    expect(result.unresolvedS).toBeCloseTo(13.3)
  })

  it('measures coverage over the transcript’s non-ad time only', () => {
    const result = classifyAlignment(spans, 3290, 3335, segments)
    // 2623 + 593.3 of 2626.6 + 603 seconds of content
    expect(result.coverage).toBeCloseTo((2623 + 593.3) / (2626.6 + 603), 6)
  })

  it('counts what is left of the listener’s copy as its own ads', () => {
    const result = classifyAlignment(spans, 3290, 3335, segments)
    expect(result.listenerAdsS).toBeCloseTo(3335 - (2623 + 593.3) - 13.3)
  })

  it('treats time under no segment as absent content', () => {
    const result = classifyAlignment([{ srcStartS: 10, srcEndS: 50, offsetS: 0 }], 60, 60, [
      { startS: 10, endS: 50, isAd: false },
    ])
    expect(result.gaps).toEqual([
      { srcStartS: 0, srcEndS: 10, kind: 'confirmed' },
      { srcStartS: 50, srcEndS: 60, kind: 'confirmed' },
    ])
    expect(result.coverage).toBe(1)
  })

  it('folds slivers into their neighbour', () => {
    const result = classifyAlignment(
      [
        { srcStartS: 0, srcEndS: 20, offsetS: 0 },
        { srcStartS: 30, srcEndS: 50, offsetS: 5 },
      ],
      50,
      55,
      [
        { startS: 0, endS: 20.1, isAd: false }, // 0.1 s past the span's end
        { startS: 20.1, endS: 30, isAd: true },
        { startS: 30, endS: 50, isAd: false },
      ],
    )
    expect(result.gaps).toEqual([{ srcStartS: 20, srcEndS: 30, kind: 'confirmed' }])
  })

  it('without spans everything under a content segment is unresolved', () => {
    const result = classifyAlignment([], 100, 100, [{ startS: 0, endS: 100, isAd: false }])
    expect(result.gaps).toEqual([{ srcStartS: 0, srcEndS: 100, kind: 'unresolved' }])
    expect(result.coverage).toBe(0)
    expect(result.listenerAdsS).toBe(0)
  })

  it('without segments falls back to the share of the track inside spans', () => {
    const result = classifyAlignment([{ srcStartS: 0, srcEndS: 90, offsetS: 0 }], 100, 100, [])
    expect(result.coverage).toBeCloseTo(0.9)
    expect(result.gaps).toEqual([{ srcStartS: 90, srcEndS: 100, kind: 'unresolved' }])
  })
})
