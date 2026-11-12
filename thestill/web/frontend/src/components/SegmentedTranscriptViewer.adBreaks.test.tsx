// Spec #116 §6.1 — ad breaks as one row.
//
// The player context is mocked (as in the karaoke tests) so a test can put a
// timeline map in force, or an ad of the listener's own "playing", without
// audio. With `timelineMap: null` the rows are in their plain state.

import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AnnotatedSegment, AnnotatedTranscriptDump, SegmentKind } from '../api/types'
import { TimelineMap, type ListenerAd } from '../contexts/playback-engine/timeline-map'
import { __resetDeepLinkSeekForTests } from '../hooks/useDeepLinkSeek'
import SegmentedTranscriptViewer from './SegmentedTranscriptViewer'
import { ToastProvider } from './Toast'

const player = {
  track: { episodeId: 'ep1', podcastSlug: 'p', episodeSlug: 'e', title: '', audioUrl: '' } as { episodeId: string } | null,
  isPlaying: false,
  isLoading: false,
  timelineMap: null as TimelineMap | null,
  listenerAd: null as ListenerAd | null,
  nativeDuration: null as number | null,
  assetTime: 0,
  time: 0,
}

vi.mock('../contexts/PlayerContext', () => ({
  usePlayer: () => ({
    track: player.track,
    isPlaying: player.isPlaying,
    isLoading: player.isLoading,
    timelineMap: player.timelineMap,
    listenerAd: player.listenerAd,
    nativeDuration: player.nativeDuration,
    getAssetTime: () => player.assetTime,
    getCurrentTime: () => player.time,
  }),
  usePlayerTime: () => player.time,
  PlayerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

const track = vi.fn()
vi.mock('../analytics/client', () => ({ track: (...args: unknown[]) => track(...args) }))

function seg(o: Partial<AnnotatedSegment> & { id: number; start: number; end: number }): AnnotatedSegment {
  return {
    id: o.id,
    start: o.start,
    end: o.end,
    speaker: o.speaker ?? 'Host',
    text: o.text ?? `passage ${o.id}`,
    kind: (o.kind ?? 'content') as SegmentKind,
    sponsor: o.sponsor ?? null,
    source_segment_ids: [o.id],
    source_word_span: null,
    user_segment_id: null,
    metadata: {},
  }
}

// 0–20 s episode; a break of two ads with a filler between (20–26 s);
// 26–46 s episode; a second break (46–50 s); 50–60 s episode.
function transcript(): AnnotatedTranscriptDump {
  return {
    episode_id: 'ep1',
    segments: [
      seg({ id: 1, start: 0, end: 20, text: 'First part of the episode' }),
      seg({ id: 2, start: 20, end: 23, kind: 'ad_break', sponsor: 'Navan', text: 'Navan makes travel easy' }),
      seg({ id: 3, start: 23, end: 24, kind: 'filler', text: 'uh and' }),
      seg({ id: 4, start: 24, end: 26, kind: 'ad_break', sponsor: 'Shopify', text: 'Shopify powers your store' }),
      seg({ id: 5, start: 26, end: 46, text: 'Second part of the episode' }),
      seg({ id: 6, start: 46, end: 50, kind: 'ad_break', sponsor: 'Navan', text: 'Navan again' }),
      seg({ id: 7, start: 50, end: 60, text: 'Third part of the episode' }),
    ],
    playback_time_offset_seconds: 0,
    algorithm_version: 'v1',
    transcript_source_duration_s: null,
    cleaning: null,
  }
}

type Props = Partial<Parameters<typeof SegmentedTranscriptViewer>[0]>
const ui = (props: Props = {}) => (
  <ToastProvider>
    <SegmentedTranscriptViewer transcript={transcript()} episodeId="ep1" {...props} />
  </ToastProvider>
)
const text = (id: number) => screen.getByTestId(`ad-break-text-${id}`).textContent

// The listener's copy: the first break replaced by a 15 s ad, the second cut.
const listenerMap = new TimelineMap(
  [
    { srcStartS: 0, srcEndS: 20, offsetS: 0 },
    { srcStartS: 26, srcEndS: 46, offsetS: 9 },
    { srcStartS: 50, srcEndS: 60, offsetS: 5 },
  ],
  [
    { srcStartS: 20, srcEndS: 26, kind: 'confirmed' },
    { srcStartS: 46, srcEndS: 50, kind: 'confirmed' },
  ],
)

describe('ad breaks as one row (spec #116 §6.1)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    window.history.replaceState(null, '', '/')
    __resetDeepLinkSeekForTests()
    track.mockReset()
    Object.assign(player, {
      track: { episodeId: 'ep1' },
      isPlaying: false,
      isLoading: false,
      timelineMap: null,
      listenerAd: null,
      nativeDuration: null,
      assetTime: 0,
      time: 0,
    })
  })

  describe('without a map', () => {
    it('draws a run of ad segments as one collapsed row with its sponsors and length', () => {
      render(ui())
      expect(screen.getAllByTestId(/^ad-break-\d+$/)).toHaveLength(2)
      expect(text(2)).toBe('Ad break · Navan, Shopify · 0:06')
      expect(text(6)).toBe('Ad break · Navan · 0:04')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-state', 'plain')
      // The ad text is not on the page until asked for.
      expect(screen.queryByText(/Navan makes travel easy/)).toBeNull()
      expect(screen.queryByText(/Shopify powers your store/)).toBeNull()
      expect(screen.queryByTestId('segment-ad_break-2')).toBeNull()
    })

    it('opens one row on Show ad text, says whose copy the text is from, and reports it', async () => {
      const user = userEvent.setup()
      render(ui())
      await user.click(screen.getByRole('button', { name: /^Show ad text\. Ad break, Navan, Shopify · 0:06/ }))
      const row = screen.getByTestId('ad-break-2')
      expect(row).toHaveAttribute('data-open', 'true')
      expect(row).toHaveTextContent('From the copy we transcribed.')
      expect(row).toHaveTextContent('Navan makes travel easy')
      expect(row).toHaveTextContent('Shopify powers your store')
      // The other break stays closed.
      expect(screen.getByTestId('ad-break-6')).toHaveAttribute('data-open', 'false')
      expect(track).toHaveBeenCalledWith('ad_break_expanded', { state: 'plain' })

      await user.click(screen.getByRole('button', { name: /^Hide ad text/ }))
      expect(row).toHaveAttribute('data-open', 'false')
      expect(track).toHaveBeenCalledTimes(1)
    })

    it('seeks to the break’s start on a click on the row, not on the toggle', async () => {
      const user = userEvent.setup()
      const onSeek = vi.fn()
      render(ui({ onSeekRequest: onSeek }))
      await user.click(screen.getByRole('button', { name: /^Show ad text\. Ad break, Navan, Shopify/ }))
      expect(onSeek).not.toHaveBeenCalled()
      fireEvent.click(screen.getByTestId('ad-break-6'))
      expect(onSeek).toHaveBeenCalledWith(46)
      // An open row's own segments seek to themselves, once.
      onSeek.mockClear()
      fireEvent.click(screen.getByTestId('segment-ad_break-4'))
      expect(onSeek).toHaveBeenCalledTimes(1)
      expect(onSeek).toHaveBeenCalledWith(24)
    })

    it('opens a row for a search match in its text or sponsor, and closes it when the search is cleared', async () => {
      const user = userEvent.setup()
      render(ui())
      const search = screen.getByRole('searchbox', { name: 'Search transcript' })
      await user.type(search, 'Shopify')
      await screen.findByText(/powers your store/)
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'true')
      // The break without a match is folded away with the other non-matches.
      expect(screen.queryByTestId('ad-break-6')).toBeNull()

      await user.clear(search)
      await screen.findByTestId('ad-break-6')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'false')
    })

    it('opens the row for a jump to something inside it, and leaves it closed for a link to a moment', () => {
      const { rerender } = render(ui({ scrollToSegmentId: { segmentId: 4, nonce: 1 } }))
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'false')
      rerender(ui({ scrollToSegmentId: { segmentId: 4, nonce: 2, reveal: true } }))
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'true')
    })

    it('shows the row, open, for an entity filter that selects one of its segments', () => {
      render(ui({ visibleSegmentIds: new Set([4, 5]) }))
      const row = screen.getByTestId('ad-break-2')
      expect(row).toHaveAttribute('data-open', 'true')
      expect(row).toHaveTextContent('Shopify powers your store')
      expect(row).not.toHaveTextContent('Navan makes travel easy')
    })

    it('scrolls to the row for any of its segments, hidden filler included', () => {
      const scrolled: Element[] = []
      const original = Element.prototype.scrollIntoView
      Element.prototype.scrollIntoView = function (this: Element) {
        scrolled.push(this)
      }
      try {
        const { rerender } = render(ui({ scrollToSegmentId: { segmentId: 4, nonce: 1 } }))
        rerender(ui({ scrollToSegmentId: { segmentId: 3, nonce: 2 } }))
        expect(scrolled).toEqual([screen.getByTestId('ad-break-2'), screen.getByTestId('ad-break-2')])
      } finally {
        Element.prototype.scrollIntoView = original
      }
    })

    it('puts a cold link’s Play from on the row, collapsed', () => {
      window.history.replaceState(null, '', '/podcasts/show/episodes/ep#t=24')
      render(ui({ onSeekRequest: vi.fn() }))
      const play = screen.getByRole('button', { name: 'Play from 00:24' })
      expect(play.compareDocumentPosition(screen.getByTestId('ad-break-2')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'false')
    })

    it('is the active row while the clock is inside the break', () => {
      player.time = 24.5
      render(ui())
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-active', 'true')
      expect(screen.getByTestId('ad-break-6')).toHaveAttribute('data-active', 'false')
    })
  })

  describe('with a map in force', () => {
    beforeEach(() => {
      player.timelineMap = listenerMap
    })

    it('says what the listener’s copy has at each break, and drops the sponsors it does not carry', () => {
      render(ui())
      expect(text(2)).toBe('Ad break · different in your copy · 0:15')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-state', 'different')
      expect(text(6)).toBe('Ad break · not in your copy')
      expect(screen.getByTestId('ad-break-6')).toHaveAttribute('data-state', 'absent')
    })

    it('keeps the sponsors for a break that plays as transcribed, and flags one the aligner could not place', () => {
      player.timelineMap = new TimelineMap([
        { srcStartS: 0, srcEndS: 30, offsetS: 0 }, // the first break lies inside
        { srcStartS: 50, srcEndS: 60, offsetS: 4 }, // nothing says what 46–50 is
      ])
      render(ui())
      expect(text(2)).toBe('Ad break · Navan, Shopify · 0:06')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-state', 'same')
      expect(text(6)).toBe('Ad break · Navan · 0:04 · timing approximate')
      expect(screen.getByTestId('ad-break-6')).toHaveAttribute('data-state', 'unresolved')
    })

    it('does not seek from a break that is gone from the copy, and says so when opened', async () => {
      const user = userEvent.setup()
      const onSeek = vi.fn()
      render(ui({ onSeekRequest: onSeek }))
      fireEvent.click(screen.getByTestId('ad-break-2'))
      expect(onSeek).not.toHaveBeenCalled()

      await user.click(screen.getByRole('button', { name: /^Show ad text\. Ad break, different in your copy · 0:15/ }))
      const row = screen.getByTestId('ad-break-2')
      expect(row).toHaveTextContent('From the copy we transcribed. Your copy plays other ads here.')
      expect(track).toHaveBeenCalledWith('ad_break_expanded', { state: 'different' })
      fireEvent.click(screen.getByText(/Navan makes travel easy/))
      expect(onSeek).not.toHaveBeenCalled()
    })

    it('changes only the text when the map arrives or is dropped: same rows, an open row stays open', async () => {
      const user = userEvent.setup()
      player.timelineMap = null
      const { rerender } = render(ui())
      await user.click(screen.getByRole('button', { name: /^Show ad text\. Ad break, Navan, Shopify/ }))
      const rowsBefore = screen.getAllByTestId(/^(ad-break|segment-content)-\d+$/).map((el) => el.dataset.testid)

      player.timelineMap = listenerMap
      rerender(ui())
      expect(text(2)).toBe('Ad break · different in your copy · 0:15')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'true')
      expect(screen.getAllByTestId(/^(ad-break|segment-content)-\d+$/).map((el) => el.dataset.testid)).toEqual(rowsBefore)

      player.timelineMap = null
      rerender(ui())
      expect(text(2)).toBe('Ad break · Navan, Shopify · 0:06')
      expect(screen.getByTestId('ad-break-2')).toHaveAttribute('data-open', 'true')
    })

    it('leaves the rows plain for another episode’s audio', () => {
      player.track = { episodeId: 'other' }
      render(ui())
      expect(text(2)).toBe('Ad break · Navan, Shopify · 0:06')
    })

    it('while an ad of the listener’s own plays at a break: that row is active and counts down', () => {
      vi.useFakeTimers()
      try {
        player.isPlaying = true
        player.time = 20 // the clock is held where the ad interrupted
        player.assetTime = 23
        player.listenerAd = listenerMap.listenerAdAt(23)
        expect(player.listenerAd).toEqual({ startS: 20, endS: 35, srcFromS: 20, srcToS: 26 })
        render(ui())
        const row = screen.getByTestId('ad-break-2')
        expect(row).toHaveAttribute('data-state', 'playing')
        expect(row).toHaveAttribute('data-active', 'true')
        expect(text(2)).toBe('Ad playing · 0:12 left')
        // The passage the clock is held on is not also active.
        expect(screen.getByTestId('segment-content-1')).toHaveAttribute('data-active', 'false')

        player.assetTime = 30
        act(() => vi.advanceTimersByTime(600))
        expect(text(2)).toBe('Ad playing · 0:05 left')
      } finally {
        vi.useRealTimers()
      }
    })

    it('adds no row for an ad that only the listener’s copy has; the passage stays active', () => {
      // A map with an ad of the listener's own in the middle of the first part.
      const map = new TimelineMap([
        { srcStartS: 0, srcEndS: 10, offsetS: 0 },
        { srcStartS: 10, srcEndS: 60, offsetS: 30 },
      ])
      player.timelineMap = map
      player.isPlaying = true
      player.time = 10
      player.assetTime = 25
      player.listenerAd = map.listenerAdAt(25)
      expect(player.listenerAd).not.toBeNull()
      render(ui())
      expect(screen.getAllByTestId(/^ad-break-\d+$/)).toHaveLength(2)
      expect(screen.queryByText(/Ad playing/)).toBeNull()
      expect(screen.getByTestId('segment-content-1')).toHaveAttribute('data-active', 'true')
    })
  })
})
