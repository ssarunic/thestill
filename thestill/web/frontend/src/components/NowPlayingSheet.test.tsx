// Spec #72 — the expanded Now Playing surface: phone sheet and desktop card.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from '../contexts/PlayerContext'
import NowPlayingSheet from './NowPlayingSheet'
import { __resetFollowPlaybackForTests } from '../hooks/useFollowPlayback'

// Entities for the tick row come from the reader's query hook; stub it so
// the sheet test needs no QueryClient. Two people, three mentions.
vi.mock('../hooks/useApi', () => ({
  useEpisodeEntities: vi.fn((episodeId: string | null) => ({
    data: episodeId
      ? {
          entities: [
            {
              entity: { id: 'person:ed', type: 'person', canonical_name: 'Ed Elson', wikidata_qid: null },
              mention_count: 2,
              first_mention_ms: 600_000,
              speaker_kind: 'host',
              salience: 0.9,
              mentions: [
                { id: 'm1', start_ms: 600_000, end_ms: 601_000, segment_id: 1, surface_form: 'Ed', confidence: 1 },
                { id: 'm2', start_ms: 1_800_000, end_ms: 1_801_000, segment_id: 2, surface_form: 'Ed', confidence: 1 },
              ],
            },
            {
              entity: { id: 'company:openai', type: 'company', canonical_name: 'OpenAI', wikidata_qid: null },
              mention_count: 1,
              first_mention_ms: 900_000,
              speaker_kind: null,
              salience: 0.5,
              mentions: [{ id: 'm3', start_ms: 900_000, end_ms: 901_000, segment_id: 3, surface_form: 'OpenAI', confidence: 1 }],
            },
          ],
        }
      : undefined,
  })),
}))

// The form is chosen by the sm breakpoint; drive it per test.
const isSmUp = { current: true }
vi.mock('../hooks/useMediaQuery', () => ({
  useIsSmUp: () => isSmUp.current,
  useMediaQuery: () => false,
}))

const ctxHolder: { current: PlayerContextValue | null } = { current: null }
const ctx = new Proxy({} as PlayerContextValue, {
  get: (_target, prop) => ctxHolder.current![prop as keyof PlayerContextValue],
})

function Probe() {
  const player = usePlayer()
  useEffect(() => {
    ctxHolder.current = player
  })
  return null
}

function LocationProbe() {
  const location = useLocation()
  const background = (location.state as { backgroundLocation?: { pathname: string } } | null)?.backgroundLocation
  return (
    <div data-testid="location">
      {location.pathname}{location.search}|{background?.pathname ?? 'none'}
    </div>
  )
}

const track: PlayerTrack = {
  episodeId: 'ep-1',
  podcastSlug: 'pod',
  episodeSlug: 'ep-1-slug',
  title: 'Audio Episode',
  podcastTitle: 'The Pod',
  audioUrl: 'https://cdn.test/ep.mp3',
  durationHint: 3600,
}
const episodePath = '/podcasts/pod/episodes/ep-1-slug'

const videoTrack: PlayerTrack = {
  ...track,
  episodeId: 'ep-video',
  episodeSlug: 'ep-video-slug',
  audioUrl: 'https://cdn.test/ep2.mp4',
  playback: {
    kind: 'video',
    video: { url: 'https://cdn.test/ep2.mp4', mime_type: 'video/mp4', timeline_offset: 0 },
    audio: { url: 'https://cdn.test/ep2.mp3', mime_type: 'audio/mpeg', timeline_offset: 0 },
    poster_url: null,
    captions_url: null,
  },
}

function tree(initialPath: string, isOpen: boolean, onClose: () => void) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <PlayerProvider>
        <Probe />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
        <button type="button" aria-haspopup="dialog" aria-expanded={isOpen}>
          opener
        </button>
        <button type="button">elsewhere</button>
        <NowPlayingSheet isOpen={isOpen} onClose={onClose} />
      </PlayerProvider>
    </MemoryRouter>
  )
}

function renderSheet(initialPath = '/inbox', props: { isOpen?: boolean; onClose?: () => void } = {}) {
  const onClose = props.onClose ?? vi.fn()
  const isOpen = props.isOpen ?? true
  const utils = render(tree(initialPath, isOpen, onClose))
  const video = document.querySelector('video') as HTMLVideoElement
  const setOpen = (open: boolean) => utils.rerender(tree(initialPath, open, onClose))
  return { ...utils, onClose, video, setOpen }
}

const mediaLayer = () => document.querySelector('[data-testid="player-media-layer"]') as HTMLElement

// Minimal media stubs so toggle/setRate/seek have observable effects in jsdom.
const state = { paused: true, rate: 1, time: 0 }
beforeEach(() => {
  state.paused = true
  state.rate = 1
  state.time = 0
  localStorage.clear()
  __resetFollowPlaybackForTests()
  isSmUp.current = true
  vi.spyOn(HTMLMediaElement.prototype, 'paused', 'get').mockImplementation(() => state.paused)
  vi.spyOn(HTMLMediaElement.prototype, 'playbackRate', 'get').mockImplementation(() => state.rate)
  vi.spyOn(HTMLMediaElement.prototype, 'playbackRate', 'set').mockImplementation((v: number) => {
    state.rate = v
  })
  vi.spyOn(HTMLMediaElement.prototype, 'defaultPlaybackRate', 'set').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'get').mockImplementation(() => state.time)
  vi.spyOn(HTMLMediaElement.prototype, 'currentTime', 'set').mockImplementation((v: number) => {
    state.time = v
  })
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    state.paused = false
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    state.paused = true
    this.dispatchEvent(new Event('pause'))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.style.overflow = ''
})

describe('NowPlayingSheet (spec #72)', () => {
  it('renders nothing while closed or without a track', () => {
    const { rerender } = renderSheet('/inbox', { isOpen: true })
    expect(screen.queryByRole('dialog', { name: 'Now playing' })).not.toBeInTheDocument()
    act(() => ctx.play(track))
    expect(screen.getByRole('dialog', { name: 'Now playing' })).toBeInTheDocument()
    rerender(
      <MemoryRouter initialEntries={['/inbox']}>
        <PlayerProvider>
          <NowPlayingSheet isOpen={false} onClose={() => {}} />
        </PlayerProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByRole('dialog', { name: 'Now playing' })).not.toBeInTheDocument()
  })

  it('desktop card: not modal, sits on the transient rung, closes on Esc and click-outside but not on its opener', () => {
    const { onClose } = renderSheet()
    act(() => ctx.play(track))
    const dialog = screen.getByRole('dialog', { name: 'Now playing' })
    expect(dialog).not.toHaveAttribute('aria-modal')
    expect(dialog).toHaveAttribute('data-media-host', 'now-playing')
    expect(screen.getByTestId('now-playing-root')).toHaveClass('z-[70]')
    expect(screen.getByTestId('now-playing-root')).toHaveStyle({ bottom: 'calc(var(--player-h, 0px) + 0.5rem)' })
    expect(document.body.style.overflow).toBe('')

    fireEvent.mouseDown(screen.getByRole('button', { name: 'opener' }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Pause' }))
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.mouseDown(screen.getByRole('button', { name: 'elsewhere' }))
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('phone sheet: modal, locks the page, closes on scrim tap and on a long enough swipe down', () => {
    isSmUp.current = false
    const { onClose } = renderSheet()
    act(() => ctx.play(track))
    const dialog = screen.getByRole('dialog', { name: 'Now playing' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(document.body.style.overflow).toBe('hidden')
    expect(document.activeElement).toBe(dialog)
    // No volume on phones.
    expect(screen.queryByLabelText('Volume')).not.toBeInTheDocument()

    const scrim = screen.getByTestId('now-playing-root').firstElementChild as HTMLElement
    fireEvent.click(scrim)
    expect(onClose).toHaveBeenCalledTimes(1)

    const handle = screen.getByTestId('now-playing-drag-handle')
    handle.setPointerCapture = () => {}
    fireEvent.pointerDown(handle, { pointerId: 1, clientY: 100 })
    fireEvent.pointerMove(handle, { pointerId: 1, clientY: 130 })
    fireEvent.pointerUp(handle, { pointerId: 1, clientY: 130 })
    expect(onClose).toHaveBeenCalledTimes(1) // 30 px is a nudge, not a dismissal
    fireEvent.pointerDown(handle, { pointerId: 2, clientY: 100 })
    fireEvent.pointerUp(handle, { pointerId: 2, clientY: 200 })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('transport, speed and seek drive the player; speed persists', async () => {
    const { video } = renderSheet()
    act(() => ctx.play(track))
    expect(ctx.isPlaying).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(ctx.isPlaying).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(ctx.isPlaying).toBe(true)

    act(() => {
      video.dispatchEvent(new Event('durationchange'))
    })
    fireEvent.change(screen.getByLabelText('Seek'), { target: { value: '600' } })
    expect(state.time).toBe(600)
    await userEvent.click(screen.getByRole('button', { name: 'Forward 15 seconds' }))
    expect(state.time).toBe(615)

    await userEvent.click(screen.getByRole('radio', { name: '1.5×' }))
    expect(state.rate).toBe(1.5)
    expect(localStorage.getItem('thestill:player:rate')).toBe('1.5')
  })

  it('Stop clears the session and closes', async () => {
    const { onClose } = renderSheet()
    act(() => ctx.play(track))
    await userEvent.click(screen.getByRole('button', { name: 'Stop playback' }))
    expect(ctx.track).toBeNull()
    expect(onClose).toHaveBeenCalled()
  })

  it('the title link keeps the inbox overlay contract', async () => {
    renderSheet('/inbox')
    act(() => ctx.play(track))
    await userEvent.click(screen.getByRole('link', { name: 'Audio Episode' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}|/inbox`)
  })

  it('entity ticks sit at mention positions on the scrubber and seek on tap', async () => {
    renderSheet()
    act(() => ctx.play(track)) // durationHint 3600 → duration known at once
    const ticks = screen.getByTestId('scrubber-ticks')
    expect(ticks.querySelectorAll('button')).toHaveLength(3)
    const ed = screen.getByRole('button', { name: 'Ed Elson at 10:00' })
    expect(ed.style.left).toBe(`${(600 / 3600) * 100}%`)
    await userEvent.click(ed)
    expect(state.time).toBe(600)
  })

  it('Open transcript here deep-links to the current moment with the overlay contract, and closes', async () => {
    const { onClose, video } = renderSheet('/inbox')
    act(() => ctx.play(track))
    act(() => {
      // The sheet reads the 4 Hz time context, which follows `timeupdate`.
      state.time = 754
      video.dispatchEvent(new Event('timeupdate'))
    })
    await userEvent.click(screen.getByRole('link', { name: 'Open transcript here' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}?view=transcript&t=754|/inbox`)
    expect(onClose).toHaveBeenCalled()
  })

  it('the follow-playback toggle writes the shared preference', async () => {
    renderSheet()
    act(() => ctx.play(track))
    const toggle = screen.getByRole('button', { name: 'Follow playback' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'Following playback' })).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem('thestill:transcript:followPlayback')).toBe('true')
  })

  it('phone: hosts the video when nothing else presents it, and releases it on close (2c)', () => {
    isSmUp.current = false
    const { setOpen } = renderSheet()
    act(() => ctx.play(videoTrack))
    expect(screen.getByTestId('now-playing-video-slot')).toBeInTheDocument()
    expect(ctx.presentation).toBe('theater')
    expect(mediaLayer().style.zIndex).toBe('71')

    act(() => setOpen(false))
    // The slot is released: the machine falls back to `floating`, which on a
    // phone presents nothing (the tile is desktop-only) — audio-first, still
    // playing.
    expect(ctx.presentation).toBe('floating')
    expect(mediaLayer().style.visibility).toBe('hidden')
    expect(ctx.isPlaying).toBe(true)
  })

  it('phone: yields to a theater slot the reader already holds (2c)', () => {
    isSmUp.current = false
    const { setOpen } = renderSheet('/inbox', { isOpen: false })
    act(() => ctx.play(videoTrack))
    const readerSlot = document.createElement('div')
    document.body.appendChild(readerSlot)
    act(() => {
      ctx.registerTheaterSlot('ep-video', readerSlot)
    })
    expect(mediaLayer().style.zIndex).toBe('40')

    act(() => setOpen(true))
    expect(ctx.presentation).toBe('theater')
    expect(mediaLayer().style.zIndex).toBe('40') // still the reader's slot
  })

  it('desktop: no video slot; Hide/Show video toggles the visual preference (2c)', async () => {
    renderSheet()
    act(() => ctx.play(videoTrack))
    expect(screen.queryByTestId('now-playing-video-slot')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Hide video' }))
    expect(ctx.videoPreference).toBe('audio-only')
    await userEvent.click(screen.getByRole('button', { name: 'Show video' }))
    expect(ctx.videoPreference).toBe('shown')
    // Audio-only tracks get neither chip.
    act(() => ctx.play(track))
    expect(screen.queryByRole('button', { name: /video/i })).not.toBeInTheDocument()
  })

  it('closing never touches playback', () => {
    const { onClose } = renderSheet()
    act(() => ctx.play(track))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
    expect(ctx.isPlaying).toBe(true)
  })
})
