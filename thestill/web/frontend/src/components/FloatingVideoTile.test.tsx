// Spec #61 §2 — floating tile: shown off-reader, close drops to audio-first.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from '../contexts/PlayerContext'
import FloatingVideoTile from './FloatingVideoTile'
import type { PlaybackManifest } from '../api/types'

// Captured in an effect (not during render, per react-hooks/globals) and
// read through a proxy so assertions always see the latest context value.
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

const manifest: PlaybackManifest = {
  kind: 'video',
  video: { url: 'https://cdn.test/ep.mp4', mime_type: 'video/mp4', timeline_offset: 0 },
  audio: null,
  poster_url: null,
  captions_url: null,
}

const videoTrack: PlayerTrack = {
  episodeId: 'ep-1',
  podcastSlug: 'pod',
  episodeSlug: 'ep-1-slug',
  title: 'Video Episode',
  audioUrl: 'https://cdn.test/ep.mp4',
  playback: manifest,
}

function renderTile() {
  return render(
    <MemoryRouter>
      <PlayerProvider>
        <Probe />
        <FloatingVideoTile />
      </PlayerProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (this: HTMLMediaElement) {
    this.dispatchEvent(new Event('pause'))
  })
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('FloatingVideoTile', () => {
  it('is absent while nothing plays', () => {
    renderTile()
    expect(screen.queryByTestId('floating-video-tile')).not.toBeInTheDocument()
  })

  it('appears for off-reader video playback and registers the floating slot', () => {
    renderTile()
    act(() => ctx.play(videoTrack))
    expect(ctx.presentation).toBe('floating')
    expect(screen.getByTestId('floating-video-tile')).toBeInTheDocument()
    expect(screen.getByTestId('floating-video-slot')).toBeInTheDocument()
    expect(screen.getByText('Video Episode')).toBeInTheDocument()
  })

  it('stays hidden for audio tracks', () => {
    renderTile()
    act(() =>
      ctx.play({
        episodeId: 'ep-audio',
        podcastSlug: 'pod',
        episodeSlug: 'a',
        title: 'Audio',
        audioUrl: 'https://cdn.test/a.mp3',
      }),
    )
    expect(screen.queryByTestId('floating-video-tile')).not.toBeInTheDocument()
  })

  it('close drops to audio-first without touching playback', async () => {
    const user = userEvent.setup()
    renderTile()
    act(() => ctx.play(videoTrack))
    await user.click(screen.getByRole('button', { name: 'Close video (keep listening)' }))

    expect(screen.queryByTestId('floating-video-tile')).not.toBeInTheDocument()
    expect(ctx.videoPreference).toBe('audio-only')
    expect(ctx.presentation).toBe('hidden')
    // Session survives the surface teardown.
    expect(ctx.track?.episodeId).toBe('ep-1')
  })

  it('disappears when a theater slot takes priority', () => {
    renderTile()
    act(() => ctx.play(videoTrack))
    const slot = document.createElement('div')
    let unregister!: () => void
    act(() => { unregister = ctx.registerTheaterSlot('ep-1', slot) })
    expect(screen.queryByTestId('floating-video-tile')).not.toBeInTheDocument()
    act(() => unregister())
    expect(screen.getByTestId('floating-video-tile')).toBeInTheDocument()
  })
})
