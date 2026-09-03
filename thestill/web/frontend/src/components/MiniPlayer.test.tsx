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

function renderPlayer(initialPath = '/podcasts') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <PlayerProvider>
        <Probe />
        <Routes>
          <Route path="*" element={<LocationProbe />} />
        </Routes>
        <input aria-label="Search box" />
        <MiniPlayer />
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

  it('opens the episode as an overlay when clicked from the inbox', async () => {
    renderPlayer('/inbox')
    act(() => ctx.play(track))
    await userEvent.click(screen.getByRole('link', { name: 'Audio Episode' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}|/inbox`)
  })

  it('navigates plainly from any other page', async () => {
    renderPlayer('/podcasts')
    act(() => ctx.play(track))
    await userEvent.click(screen.getByRole('link', { name: 'Audio Episode' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}|none`)
  })

  it('does not push a duplicate entry when already on the episode', async () => {
    renderPlayer(episodePath)
    act(() => ctx.play(track))
    await userEvent.click(screen.getByRole('link', { name: 'Audio Episode' }))
    expect(screen.getByTestId('location')).toHaveTextContent(`${episodePath}|none`)
  })
})
