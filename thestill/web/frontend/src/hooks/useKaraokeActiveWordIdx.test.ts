import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import type { WordTimestamp } from '../api/types'
import { useKaraokeActiveWordIdx } from './useKaraokeActiveWordIdx'

const words: WordTimestamp[] = [
  { w: 'Hello', s: 0, e: 0.5 },
  { w: 'world.', s: 0.6, e: 1.5 },
  // Deliberate long gap before the next word — the pause case the read-up-to
  // cutoff was added to handle.
  { w: 'Goodbye', s: 5.0, e: 5.9 },
]

// `getCurrentTime` is in the hook's effect deps, so each test declares its
// own stable function reference outside `renderHook` — passing an inline
// arrow would trip the effect on every render and recreate the rAF loop.
describe('useKaraokeActiveWordIdx', () => {
  it('returns { -1, -1 } when words is null', () => {
    const getCurrentTime = () => 1.0
    const { result, unmount } = renderHook(() =>
      useKaraokeActiveWordIdx(null, 0, getCurrentTime),
    )
    expect(result.current).toEqual({ activeIdx: -1, readUpTo: -1 })
    unmount()
  })

  it('sets both indices to the word containing currentTime', () => {
    const getCurrentTime = () => 1.0
    const { result, unmount } = renderHook(() =>
      useKaraokeActiveWordIdx(words, 0, getCurrentTime),
    )
    expect(result.current.activeIdx).toBe(1)
    expect(result.current.readUpTo).toBe(1)
    unmount()
  })

  it('keeps readUpTo at the last-passed word during a long pause', () => {
    // currentTime sits in the gap between word 1 (ends 1.5) and word 2
    // (starts 5.0), 2.0s past the tolerance — without the read-up-to
    // cutoff, the highlight would snap back to grey here.
    const getCurrentTime = () => 3.5
    const { result, unmount } = renderHook(() =>
      useKaraokeActiveWordIdx(words, 0, getCurrentTime),
    )
    expect(result.current.activeIdx).toBe(-1)
    expect(result.current.readUpTo).toBe(1)
    unmount()
  })

  it('respects the offset on every lookup', () => {
    const getCurrentTime = () => 10.7
    const { result, unmount } = renderHook(() =>
      useKaraokeActiveWordIdx(words, 10, getCurrentTime),
    )
    expect(result.current.activeIdx).toBe(1)
    expect(result.current.readUpTo).toBe(1)
    unmount()
  })
})
