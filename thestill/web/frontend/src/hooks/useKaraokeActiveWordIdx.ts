import { useEffect, useState } from 'react'
import type { WordTimestamp } from '../api/types'
import { findActiveWordIndex } from '../utils/wordSearch'

export interface KaraokeWordCursor {
  /** Word currently being spoken; -1 during pauses, gaps, or before
   *  the first word. Drives `aria-current` on the active span. */
  activeIdx: number
  /** Highest-indexed word whose start has been crossed by the audio
   *  cursor — i.e. the cutoff for "read" colouring. Does NOT regress
   *  on pauses (the speaker stopping doesn't unread the previous
   *  word), but DOES regress on backward seeks (rewinding past a
   *  word's start un-reads it). */
  readUpTo: number
}

/** Find the highest index whose `s + offset <= currentTime`, ignoring
 *  any upper bound. "How far has the audio cursor moved through the
 *  word stream?" — distinct from `findActiveWordIndex` which asks
 *  "which word is currently being spoken right now?" and returns -1
 *  during pauses. */
function findReadUpToIndex(
  words: ReadonlyArray<WordTimestamp>,
  currentTime: number,
  offset: number,
): number {
  if (words.length === 0) return -1
  if (!Number.isFinite(currentTime)) return -1
  let lo = 0
  let hi = words.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (words[mid].s + offset <= currentTime) {
      found = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
}

/**
 * Per-frame tracking of the karaoke cursor within a segment's word
 * list. Drives both the active-word `aria-current` and the cutoff for
 * read vs unread word colouring.
 *
 * State updates only fire when the returned object's fields actually
 * change, so the host component re-renders at per-word-transition rate
 * (a few times per second at normal speech), not 60 fps. The rAF loop
 * exists to make those transitions land within ~16 ms of the audio
 * cursor — `usePlayerTime` only ticks at the browser's 4 Hz cadence
 * and can lag or skip short words.
 */
export function useKaraokeActiveWordIdx(
  words: ReadonlyArray<WordTimestamp> | null,
  offset: number,
  getCurrentTime: () => number,
): KaraokeWordCursor {
  // Lazy-init so the FIRST render already has correct values — without
  // this, every active-segment swap would flash through { -1, -1 } for
  // one frame before the rAF catches up.
  const [cursor, setCursor] = useState<KaraokeWordCursor>(() => {
    if (!words || words.length === 0) return { activeIdx: -1, readUpTo: -1 }
    const t = getCurrentTime()
    return {
      activeIdx: findActiveWordIndex(words, t, offset),
      readUpTo: findReadUpToIndex(words, t, offset),
    }
  })

  useEffect(() => {
    if (!words || words.length === 0) {
      setCursor({ activeIdx: -1, readUpTo: -1 })
      return
    }
    // Sync once on deps change. When the active segment swaps, the
    // cursor carried over from the previous segment is wrong until
    // we re-seed it from this segment's word list.
    let lastActive = findActiveWordIndex(words, getCurrentTime(), offset)
    let lastReadUpTo = findReadUpToIndex(words, getCurrentTime(), offset)
    setCursor({ activeIdx: lastActive, readUpTo: lastReadUpTo })

    let handle = 0
    const tick = () => {
      const t = getCurrentTime()
      const nextActive = findActiveWordIndex(words, t, offset)
      const nextReadUpTo = findReadUpToIndex(words, t, offset)
      if (nextActive !== lastActive || nextReadUpTo !== lastReadUpTo) {
        lastActive = nextActive
        lastReadUpTo = nextReadUpTo
        setCursor({ activeIdx: nextActive, readUpTo: nextReadUpTo })
      }
      handle = requestAnimationFrame(tick)
    }
    handle = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(handle)
  }, [words, offset, getCurrentTime])

  return cursor
}
