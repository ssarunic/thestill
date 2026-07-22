// Spec #61 §2 — theater surface: slot registration + activation.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { PlayerProvider, usePlayer, type PlayerContextValue, type PlayerTrack } from '../contexts/PlayerContext'
import TheaterSurface from './TheaterSurface'
import type { PlaybackManifest } from '../api/types'

// Spec #62 — shared IFrame API fixture: no script tag is ever injected.
vi.mock('../contexts/playback-engine/youtube-iframe-api', async () => {
  const { fakeYouTubeApi } = await import('../contexts/playback-engine/youtube-player-fake')
  return { loadYouTubeIframeApi: vi.fn(() => Promise.resolve(fakeYouTubeApi)) }
})

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
  poster_url: 'https://cdn.test/poster.jpg',
  captions_url: null,
}

const track: PlayerTrack = {
  episodeId: 'ep-1',
  podcastSlug: 'pod',
  episodeSlug: 'ep-1-slug',
  title: 'Video Episode',
  audioUrl: 'https://cdn.test/ep.mp4',
  playback: manifest,
}

function renderSurface() {
  return render(
    <PlayerProvider>
      <Probe />
      <TheaterSurface episodeId="ep-1" posterUrl="https://cdn.test/poster.jpg" track={track} />
    </PlayerProvider>,
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

describe('TheaterSurface', () => {
  it('shows the poster with a play affordance before playback', () => {
    renderSurface()
    expect(screen.getByRole('button', { name: 'Play video' })).toBeInTheDocument()
    expect(ctx.presentation).toBe('hidden')
  })

  it('activating starts the video session and registers the theater slot', async () => {
    const user = userEvent.setup()
    renderSurface()
    await user.click(screen.getByRole('button', { name: 'Play video' }))

    expect(ctx.isCurrent('ep-1')).toBe(true)
    expect(ctx.mediaKind).toBe('video')
    // Slot registered on activation → theater wins over floating.
    expect(ctx.presentation).toBe('theater')
  })

  it('Hide video unregisters the slot and drops to audio-first', async () => {
    const user = userEvent.setup()
    renderSurface()
    await user.click(screen.getByRole('button', { name: 'Play video' }))
    await user.click(screen.getByRole('button', { name: 'Hide video' }))

    expect(ctx.videoPreference).toBe('audio-only')
    expect(ctx.presentation).toBe('hidden')
    // Playback itself is untouched by the surface change.
    expect(ctx.track?.episodeId).toBe('ep-1')

    await user.click(screen.getByRole('button', { name: 'Show video' }))
    expect(ctx.presentation).toBe('theater')
  })

  it('does not register the slot for a different current episode', () => {
    renderSurface()
    act(() =>
      ctx.play({
        episodeId: 'other-ep',
        podcastSlug: 'pod',
        episodeSlug: 'other',
        title: 'Other',
        audioUrl: 'https://cdn.test/other.mp3',
      }),
    )
    expect(ctx.presentation).toBe('hidden')
    // Poster play affordance still offered for this episode.
    expect(screen.getByRole('button', { name: 'Play video' })).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Spec #62 — YouTube rendition menu entries
// ---------------------------------------------------------------------------

const dualTrack: PlayerTrack = {
  ...track,
  playback: {
    ...manifest,
    youtube: { video_id: 'validVID001', watch_url: 'https://www.youtube.com/watch?v=validVID001' },
  },
}

function renderDualSurface() {
  return render(
    <PlayerProvider>
      <Probe />
      <TheaterSurface episodeId="ep-1" posterUrl="https://cdn.test/poster.jpg" track={dualTrack} />
    </PlayerProvider>,
  )
}

describe('TheaterSurface — spec #62 YouTube entries', () => {
  it('offers the YouTube rendition only when the manifest carries the asset', async () => {
    const user = userEvent.setup()
    renderSurface()
    await user.click(screen.getByRole('button', { name: 'Play video' }))
    expect(screen.queryByRole('button', { name: 'Play video on YouTube player' })).not.toBeInTheDocument()
  })

  it('entering the YouTube rendition swaps the menu to "Use audio rendition" and back', async () => {
    const user = userEvent.setup()
    renderDualSurface()
    await user.click(screen.getByRole('button', { name: 'Play video' }))

    const ytButton = screen.getByRole('button', { name: 'Play video on YouTube player' })
    await act(async () => {
      ytButton.click()
    })

    expect(ctx.activeEngine).toBe('youtube')
    expect(ctx.presentation).toBe('theater') // slot stays registered — no §7 bounce
    expect(screen.queryByRole('button', { name: 'Play video on YouTube player' })).not.toBeInTheDocument()

    // A video-enclosure episode lands back on the native VIDEO rendition
    // (its manifest has no audio asset to switch to).
    await user.click(screen.getByRole('button', { name: 'Use video rendition' }))
    expect(ctx.activeEngine).toBe('native')
    expect(ctx.activeRendition).toBe('video')
  })
})
