// Spec #71 — the mini player as shell chrome: publishes its height, owns the
// space bar, carries the inbox overlay contract, keeps skips on phones.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from '../contexts/PlayerContext'
import MiniPlayer from './MiniPlayer'
import { PLAYER_HEIGHT_VAR } from '../constants/layers'

// The swipe-to-dismiss gesture is phone-only; drive the breakpoint per test.
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

// Shows where the router is and what background location the entry carries.
function LocationProbe() {
  const location = useLocation()
  const background = (location.state as { backgroundLocation?: { pathname: string } } | null)
    ?.backgroundLocation
  return (
    <div data-testid="location">
      {location.pathname}|{background?.pathname ?? 'none'}
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
}

const episodePath = '/podcasts/pod/episodes/ep-1-slug'

function renderPlayer(initialPath = '/podcasts', props: { isOpen?: boolean; onExpand?: () => void } = {}) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <PlayerProvider>
        <Probe />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
        <input aria-label="Search box" />
        <MiniPlayer {...props} />
      </PlayerProvider>
    </MemoryRouter>,
  )
}

const BAR_HEIGHT = 64

beforeEach(() => {
  // jsdom never flips `paused`, and the engine's toggle reads it; track it
  // alongside the play/pause mocks so toggle actually alternates.
  let paused = true
  vi.spyOn(HTMLMediaElement.prototype, 'paused', 'get').mockImplementation(() => paused)
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    paused = false
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    paused = true
    this.dispatchEvent(new Event('pause'))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  // jsdom lays nothing out; give the bar a height so the published variable
  // is observable.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
    return this.getAttribute('aria-label') === 'Audio player' ? BAR_HEIGHT : 0
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  isSmUp.current = true
  document.documentElement.style.removeProperty(PLAYER_HEIGHT_VAR)
})

const playerHeight = () => document.documentElement.style.getPropertyValue(PLAYER_HEIGHT_VAR)

describe('MiniPlayer (spec #71)', () => {
  it('publishes 0px while nothing is loaded and its height once a track plays', () => {
    renderPlayer()
    expect(screen.queryByRole('region', { name: 'Audio player' })).not.toBeInTheDocument()
    expect(playerHeight()).toBe('0px')

    act(() => ctx.play(track))
    expect(screen.getByRole('region', { name: 'Audio player' })).toBeInTheDocument()
    expect(playerHeight()).toBe(`${BAR_HEIGHT}px`)

    act(() => ctx.stop())
    expect(playerHeight()).toBe('0px')
  })

  it('sits on the player rung, above the reader overlay', () => {
    renderPlayer()
    act(() => ctx.play(track))
    expect(screen.getByRole('region', { name: 'Audio player' })).toHaveClass('z-50')
  })

  it('space toggles playback, but not while a text field owns the keyboard', () => {
    renderPlayer()
    act(() => ctx.play(track))
    expect(ctx.isPlaying).toBe(true)

    fireEvent.keyDown(document.body, { key: ' ' })
    expect(ctx.isPlaying).toBe(false)

    fireEvent.keyDown(screen.getByLabelText('Search box'), { key: ' ' })
    expect(ctx.isPlaying).toBe(false)

    fireEvent.keyDown(document.body, { key: ' ' })
    expect(ctx.isPlaying).toBe(true)
  })

  it('shows both skips at every size and keeps Close off the phone bar', () => {
    renderPlayer()
    act(() => ctx.play(track))
    expect(screen.getByRole('button', { name: 'Back 15 seconds' })).not.toHaveClass('hidden')
    expect(screen.getByRole('button', { name: 'Forward 15 seconds' })).not.toHaveClass('hidden')
    expect(screen.getByRole('button', { name: 'Close player' })).toHaveClass('hidden')
  })

  it('phone: a swipe down on the bar stops and dismisses; a short drag or a tap does not (spec #72)', async () => {
    isSmUp.current = false
    renderPlayer()
    act(() => ctx.play(track))
    const bar = screen.getByRole('region', { name: 'Audio player' })
    const play = screen.getByRole('button', { name: 'Pause' })

    // Short drag: released, nothing happens, and the tap it started as is
    // swallowed rather than landing on the button under the finger.
    fireEvent.pointerDown(play, { pointerId: 1, clientX: 300, clientY: 700 })
    fireEvent.pointerMove(play, { pointerId: 1, clientX: 300, clientY: 720 })
    expect(bar.style.transform).toBe('translateY(20px)')
    fireEvent.pointerUp(play, { pointerId: 1, clientX: 300, clientY: 720 })
    fireEvent.click(play)
    expect(ctx.track).not.toBeNull()
    expect(ctx.isPlaying).toBe(true)
    expect(bar.style.transform).toBe('')

    // A plain tap still reaches the control.
    fireEvent.pointerDown(play, { pointerId: 2, clientX: 300, clientY: 700 })
    fireEvent.pointerUp(play, { pointerId: 2, clientX: 300, clientY: 700 })
    await userEvent.click(play)
    expect(ctx.isPlaying).toBe(false)

    // A sideways drag is not a swipe down.
    fireEvent.pointerDown(bar, { pointerId: 3, clientX: 100, clientY: 700 })
    fireEvent.pointerMove(bar, { pointerId: 3, clientX: 200, clientY: 760 })
    fireEvent.pointerUp(bar, { pointerId: 3, clientX: 200, clientY: 760 })
    expect(ctx.track).not.toBeNull()

    // Past the threshold: the session is cleared and the bar is gone.
    fireEvent.pointerDown(bar, { pointerId: 4, clientX: 100, clientY: 700 })
    fireEvent.pointerMove(bar, { pointerId: 4, clientX: 100, clientY: 760 })
    fireEvent.pointerUp(bar, { pointerId: 4, clientX: 100, clientY: 760 })
    expect(ctx.track).toBeNull()
    expect(screen.queryByRole('region', { name: 'Audio player' })).toBeNull()
  })

  it('phone: scrubbing the seek slider never dismisses, and an unclosed gesture does not wedge the bar (spec #72)', async () => {
    isSmUp.current = false
    renderPlayer()
    act(() => ctx.play(track))
    const bar = screen.getByRole('region', { name: 'Audio player' })
    const seek = screen.getByLabelText('Seek')

    // A scrub that drifts downward past the dismiss travel is still a scrub.
    fireEvent.pointerDown(seek, { pointerId: 1, clientX: 100, clientY: 690 })
    fireEvent.pointerMove(seek, { pointerId: 1, clientX: 100, clientY: 760 })
    fireEvent.pointerUp(seek, { pointerId: 1, clientX: 100, clientY: 760 })
    expect(ctx.track).not.toBeNull()
    expect(bar.style.transform).toBe('')

    // A mouse press that ends off the bar leaves no pointerup behind; the
    // next press starts clean rather than finding the bar wedged.
    fireEvent.pointerDown(bar, { pointerId: 2, clientX: 100, clientY: 700 })
    fireEvent.pointerMove(bar, { pointerId: 2, clientX: 100, clientY: 730 })
    expect(bar.style.transform).toBe('translateY(30px)')
    fireEvent.pointerDown(bar, { pointerId: 3, clientX: 100, clientY: 700 })
    expect(bar.style.transform).toBe('')
    fireEvent.pointerMove(bar, { pointerId: 3, clientX: 100, clientY: 760 })
    fireEvent.pointerUp(bar, { pointerId: 3, clientX: 100, clientY: 760 })
    expect(ctx.track).toBeNull()
  })

  it('desktop: dragging the bar does nothing', () => {
    renderPlayer()
    act(() => ctx.play(track))
    const bar = screen.getByRole('region', { name: 'Audio player' })
    fireEvent.pointerDown(bar, { pointerId: 1, clientX: 100, clientY: 700 })
    fireEvent.pointerMove(bar, { pointerId: 1, clientX: 100, clientY: 800 })
    fireEvent.pointerUp(bar, { pointerId: 1, clientX: 100, clientY: 800 })
    expect(ctx.track).not.toBeNull()
  })

  it('the artwork/title block is the expand affordance (spec #72)', async () => {
    const onExpand = vi.fn()
    renderPlayer('/inbox', { onExpand })
    act(() => ctx.play(track))
    const expand = screen.getByRole('button', { name: 'Now playing: Audio Episode' })
    expect(expand).toHaveAttribute('aria-expanded', 'false')
    expect(expand).toHaveAttribute('aria-haspopup', 'dialog')
    expect(screen.queryByRole('link', { name: 'Audio Episode' })).not.toBeInTheDocument()
    await userEvent.click(expand)
    expect(onExpand).toHaveBeenCalledTimes(1)
    // Expanding never navigates.
    expect(screen.getByTestId('location')).toHaveTextContent('/inbox|none')
  })

  it('reflects the open state on the expand button', () => {
    renderPlayer('/inbox', { isOpen: true })
    act(() => ctx.play(track))
    expect(screen.getByRole('button', { name: 'Now playing: Audio Episode' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('the Show video link keeps the inbox overlay contract', async () => {
    renderPlayer('/inbox')
    act(() =>
      ctx.play({
        ...track,
        playback: {
          kind: 'audio',
          audio: null,
          video: null,
          youtube: { video_id: 'validVID001', watch_url: 'https://www.youtube.com/watch?v=validVID001' },
          poster_url: null,
          captions_url: null,
        },
      }),
    )
    await userEvent.click(screen.getByRole('link', { name: 'Show video' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}|/inbox`)
  })
})
