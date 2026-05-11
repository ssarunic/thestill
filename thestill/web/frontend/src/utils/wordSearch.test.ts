import { describe, expect, it } from 'vitest'
import type { WordTimestamp } from '../api/types'
import { findActiveWordIndex } from './wordSearch'

function word(text: string, s: number, e: number): WordTimestamp {
  return { w: text, s, e }
}

describe('findActiveWordIndex', () => {
  const words: WordTimestamp[] = [
    word('Hello', 0.0, 0.5),
    word('world.', 0.6, 1.5),
    word('Goodbye', 2.0, 2.9),
    word('world.', 3.0, 3.8),
  ]

  it('returns -1 for empty lists', () => {
    expect(findActiveWordIndex([], 1.0, 0)).toBe(-1)
  })

  it('returns -1 before the first word starts', () => {
    expect(findActiveWordIndex(words, -0.1, 0)).toBe(-1)
  })

  it('returns the word containing the current time', () => {
    expect(findActiveWordIndex(words, 0.2, 0)).toBe(0)
    expect(findActiveWordIndex(words, 1.0, 0)).toBe(1)
    expect(findActiveWordIndex(words, 2.5, 0)).toBe(2)
  })

  it('snaps to word boundaries — start is inclusive', () => {
    expect(findActiveWordIndex(words, 0.0, 0)).toBe(0)
    expect(findActiveWordIndex(words, 0.6, 0)).toBe(1)
    expect(findActiveWordIndex(words, 2.0, 0)).toBe(2)
  })

  it('keeps the highlight on a word for a short gap (default tolerance)', () => {
    // 0.55s is in the gap between word 0 (ends 0.5) and word 1 (starts 0.6);
    // default tolerance 0.15 keeps it on word 0.
    expect(findActiveWordIndex(words, 0.55, 0)).toBe(0)
  })

  it('returns -1 once the gap exceeds tolerance', () => {
    // 1.8s is past word 1's end+tolerance (1.65) and before word 2 (2.0).
    expect(findActiveWordIndex(words, 1.8, 0)).toBe(-1)
  })

  it('respects the playback offset symmetrically', () => {
    expect(findActiveWordIndex(words, 10.2, 10)).toBe(0)
    expect(findActiveWordIndex(words, 12.5, 10)).toBe(2)
  })

  it('returns -1 when current time is non-finite', () => {
    expect(findActiveWordIndex(words, Number.NaN, 0)).toBe(-1)
    expect(findActiveWordIndex(words, Number.POSITIVE_INFINITY, 0)).toBe(-1)
  })

  it('honors a custom tolerance', () => {
    // Same input as the "exceeds tolerance" case, but with a 1.0s tolerance
    // we stay on word 1.
    expect(findActiveWordIndex(words, 1.8, 0, 1.0)).toBe(1)
  })
})
