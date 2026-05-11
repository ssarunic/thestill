import type { AnnotatedSegment } from '../api/types'
import { findActiveIndex } from './findActiveIndex'

/**
 * Binary-search the latest segment whose `start + offset` is ≤ `currentTime`.
 *
 * Tolerance defaults to 0.75 seconds — wide enough to keep the highlight
 * on the previous segment across a trimmed inter-segment gap, narrow
 * enough that an entire silenced minute drops the highlight.
 */
export function findActiveSegmentIndex(
  segments: ReadonlyArray<AnnotatedSegment>,
  currentTime: number,
  offset: number,
  tolerance: number = 0.75,
): number {
  return findActiveIndex(
    segments,
    (s) => s.start,
    (s) => s.end,
    currentTime,
    offset,
    tolerance,
  )
}
