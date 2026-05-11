import type { WordTimestamp } from '../api/types'
import { findActiveIndex } from './findActiveIndex'

/**
 * Binary-search the latest word whose `s + offset` is ≤ `currentTime`.
 *
 * Tolerance defaults to 0.15 seconds — much tighter than the segment
 * search's 0.75s because word gaps are sub-second and the karaoke
 * highlight needs to leave a word promptly when the audio does.
 */
export function findActiveWordIndex(
  words: ReadonlyArray<WordTimestamp>,
  currentTime: number,
  offset: number,
  tolerance: number = 0.15,
): number {
  return findActiveIndex(
    words,
    (w) => w.s,
    (w) => w.e,
    currentTime,
    offset,
    tolerance,
  )
}
