import { describe, expect, it } from 'vitest'
import type { AnnotatedSegment } from '../api/types'
import { findActiveSegmentIndex } from './transcriptSearch'

function seg(id: number, start: number, end: number, kind: AnnotatedSegment['kind'] = 'content'): AnnotatedSegment {
  return {
    id,
    start,
    end,
    speaker: 'Alice',
    text: 'hello',
    kind,
    sponsor: null,
    source_segment_ids: [],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

describe('findActiveSegmentIndex', () => {
  const segments: AnnotatedSegment[] = [
    seg(1, 0, 10),
    seg(2, 10, 20),
    seg(3, 20, 30),
    seg(4, 35, 40), // gap 30-35 (trimmed filler)
  ]

  it('returns -1 for empty lists', () => {
    expect(findActiveSegmentIndex([], 5, 0)).toBe(-1)
  })

  it('returns -1 before the first segment starts', () => {
    expect(findActiveSegmentIndex(segments, -1, 0)).toBe(-1)
  })

  it('returns the segment containing the current time', () => {
    expect(findActiveSegmentIndex(segments, 5, 0)).toBe(0)
    expect(findActiveSegmentIndex(segments, 15, 0)).toBe(1)
    expect(findActiveSegmentIndex(segments, 25, 0)).toBe(2)
  })

  it('snaps to segment boundaries', () => {
    expect(findActiveSegmentIndex(segments, 10, 0)).toBe(1)
    expect(findActiveSegmentIndex(segments, 20, 0)).toBe(2)
  })

  it('returns the latest started segment while within tolerance of a gap', () => {
    // time 30.5 is in the trimmed gap; default tolerance 0.75 keeps us on seg 2
    expect(findActiveSegmentIndex(segments, 30.5, 0)).toBe(2)
  })

  it('returns -1 once the gap is wider than tolerance', () => {
    // time 33 exceeds seg 2's end+tolerance (30.75) and is before seg 3 starts
    expect(findActiveSegmentIndex(segments, 33, 0)).toBe(-1)
  })

  it('respects the playback offset', () => {
    expect(findActiveSegmentIndex(segments, 105, 100)).toBe(0)
    expect(findActiveSegmentIndex(segments, 115, 100)).toBe(1)
  })

  it('returns -1 when current time is non-finite', () => {
    expect(findActiveSegmentIndex(segments, Number.NaN, 0)).toBe(-1)
    expect(findActiveSegmentIndex(segments, Number.POSITIVE_INFINITY, 0)).toBe(-1)
  })
})
