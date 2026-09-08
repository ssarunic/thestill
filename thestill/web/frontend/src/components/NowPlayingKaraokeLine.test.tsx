// Spec #72 §3 — the current-line karaoke strip.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import { PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from '../contexts/PlayerContext'
import NowPlayingKaraokeLine from './NowPlayingKaraokeLine'
import type { AnnotatedSegment } from '../api/types'

const seg = (id: number, start: number, end: number, text: string): AnnotatedSegment =>
  ({ id, start, end, speaker: null, text, kind: 'content', sponsor: null, source_segment_ids: [], source_word_span: null, user_segment_id: null }) as AnnotatedSegment

const transcript = {
  segments: {
    episode_id: 'ep-1',
    segments: [seg(1, 0, 10, 'first line here'), seg(2, 10, 20, 'second line here'), seg(3, 30, 40, 'third line')],
    playback_time_offset_seconds: 0,
  },
}
const wordsForTwo = [
  { w: 'second', s: 10, e: 11 },
  { w: 'line', s: 11, e: 12 },
  { w: 'here', s: 12, e: 13 },
]
const wordsState: { data: unknown } = { data: null }
const transcriptState: { data: unknown } = { data: transcript }

vi.mock('../hooks/useApi', () => ({
  useEpisodeTranscript: vi.fn(() => ({ data: transcriptState.data })),
  useEpisodeTranscriptWords: vi.fn(() => ({ data: wordsState.data })),
}))

const ctxHolder: { current: PlayerContextValue | null } = { current: null }
const ctx = new Proxy({} as PlayerContextValue, {
  get: (_t, prop) => ctxHolder.current![prop as keyof PlayerContextValue],
})
function Probe() {
  const player = usePlayer()
  useEffect(() => {
    ctxHolder.current = player
  })
  return null
}

const track: PlayerTrack = { episodeId: 'ep-1', podcastSlug: 'pod', episodeSlug: 'ep-1-slug', title: 'T', audioUrl: 'https://cdn.test/a.mp3' }

const state = { time: 0 }
beforeEach(() => {
  state.time = 0
  wordsState.data = null
  transcriptState.data = transcript
  vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'get').mockImplementation(() => state.time)
  vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set').mockImplementation((v: number) => {
    state.time = v
  })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => vi.restoreAllMocks())

function renderLine() {
  const utils = render(
    <PlayerProvider>
      <Probe />
      <NowPlayingKaraokeLine track={track} enabled />
    </PlayerProvider>,
  )
  return { ...utils, video: document.querySelector('video') as HTMLVideoElement }
}

function tickTo(video: HTMLVideoElement, seconds: number) {
  act(() => {
    state.time = seconds
    video.dispatchEvent(new Event('timeupdate'))
  })
}

describe('NowPlayingKaraokeLine (spec #72 §3)', () => {
  it('shows the segment under the playhead as plain text when no word data exists', () => {
    const { video } = renderLine()
    act(() => ctx.play(track))
    tickTo(video, 12)
    expect(screen.getByTestId('now-playing-karaoke-line')).toHaveTextContent('second line here')
    expect(screen.getByTestId('now-playing-karaoke-line')).toHaveAttribute('data-segment-id', '2')
  })

  it('applies the 150 ms lead like the transcript tracker, and hides in gaps and without a transcript', () => {
    const { video } = renderLine()
    act(() => ctx.play(track))
    // 9.9 s: the acoustic position is still in segment 1, but the lead puts
    // the highlight on segment 2 — exactly what ActiveSegmentTracker does.
    tickTo(video, 9.9)
    expect(screen.getByTestId('now-playing-karaoke-line')).toHaveAttribute('data-segment-id', '2')
    // 25 s is between segments 2 and 3 (beyond the 0.75 s tolerance).
    tickTo(video, 25)
    expect(screen.queryByTestId('now-playing-karaoke-line')).not.toBeInTheDocument()
  })

  it('wipes words with the karaoke primitives when word data exists', () => {
    wordsState.data = { episodeId: 'ep-1', offset: 0, wordsBySegmentId: new Map([[2, wordsForTwo]]) }
    const { video } = renderLine()
    act(() => ctx.play(track))
    tickTo(video, 11.5)
    const line = screen.getByTestId('now-playing-karaoke-line')
    const words = line.querySelectorAll('[data-karaoke-word]')
    expect(words).toHaveLength(3)
    expect(line).toHaveTextContent('second line here')
    // 'second' (10-11) and 'line' (11-12) are read at 11.5 (+ lead); 'here' is not.
    expect(words[0]).toHaveClass('text-gray-900')
    expect(words[1]).toHaveClass('text-gray-900')
    expect(words[2]).toHaveClass('text-gray-400')
    expect(words[1]).toHaveAttribute('aria-current', 'true')
  })
})
