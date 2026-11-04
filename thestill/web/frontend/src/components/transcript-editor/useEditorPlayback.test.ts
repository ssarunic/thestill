import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PlayerTrack } from '../../contexts/PlayerContext'
import { useEditorPlayback, type EditorPlaybackPlayer } from './useEditorPlayback'

const LOCAL = '/api/episodes/ep-1/transcript/edits/audio'
const track: PlayerTrack = {
  episodeId: 'ep-1',
  podcastSlug: 'show',
  episodeSlug: 'ep',
  title: 'Episode',
  audioUrl: LOCAL,
  playback: null,
}

function fakePlayer(overrides: Partial<EditorPlaybackPlayer> = {}): EditorPlaybackPlayer {
  return {
    track: null,
    isPlaying: false,
    activeEngine: 'native',
    duration: 0,
    play: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    toggle: vi.fn(),
    seek: vi.fn(),
    skip: vi.fn(),
    switchRendition: vi.fn(),
    isCurrent: () => false,
    getCurrentTime: () => 0,
    ...overrides,
  }
}

function render(player: EditorPlaybackPlayer, currentTime = 0) {
  return renderHook(() =>
    useEditorPlayback({ desiredTrack: track, player, currentTime, fallbackStart: 42, episodeDuration: 3600 }),
  ).result.current
}

describe('useEditorPlayback', () => {
  it('seeks and skips the global player only while it holds our source', () => {
    const player = fakePlayer({ track, isPlaying: true, isCurrent: () => true, duration: 3500 })
    const playback = render(player)
    expect(playback.onOurSource).toBe(true)
    expect(playback.duration).toBe(3500)
    playback.seekTo(10)
    expect(player.seek).toHaveBeenCalledWith(10)
    expect(player.resume).not.toHaveBeenCalled()
    playback.skipBy(-5)
    expect(player.skip).toHaveBeenCalledWith(-5)
    playback.togglePlay()
    expect(player.toggle).toHaveBeenCalled()
    expect(player.play).not.toHaveBeenCalled()
  })

  it('starts our source instead of skipping a foreign episode', () => {
    // Codex on #461: J/L used to call player.skip directly and moved
    // whatever episode the bar held.
    const player = fakePlayer({ track: { ...track, episodeId: 'other' }, isPlaying: true, isCurrent: () => false })
    const playback = render(player, 900)
    playback.skipBy(5)
    expect(player.skip).not.toHaveBeenCalled()
    expect(player.play).toHaveBeenCalledWith(track, { startAt: 5 })
    playback.pause()
    expect(player.pause).not.toHaveBeenCalled()
    playback.togglePlay()
    expect(player.play).toHaveBeenLastCalledWith(track, { startAt: 42 })
  })

  it('moves a same-episode publisher source onto the local copy from the shown playhead', () => {
    const player = fakePlayer({
      track: { ...track, audioUrl: 'https://cdn.example/ep.mp3' },
      isPlaying: true,
      isCurrent: () => true,
      getCurrentTime: () => 120,
    })
    const playback = render(player, 120)
    expect(playback.onOurSource).toBe(false)
    playback.skipBy(-5)
    expect(player.play).toHaveBeenCalledWith(track, { startAt: 115 })
    playback.togglePlay()
    expect(player.play).toHaveBeenLastCalledWith(track, { startAt: 120 })
    expect(player.switchRendition).not.toHaveBeenCalled()
    playback.pause()
    expect(player.pause).toHaveBeenCalled()
  })

  it('leaves the YouTube engine before selecting the local audio', () => {
    const player = fakePlayer({
      track: { ...track, audioUrl: 'https://cdn.example/ep.mp3' },
      activeEngine: 'youtube',
      isCurrent: () => true,
    })
    const playback = render(player)
    playback.seekTo(30)
    expect(player.switchRendition).toHaveBeenCalledWith('audio')
    expect(player.play).toHaveBeenCalledWith(track, { startAt: 30 })
    const order = [
      (player.switchRendition as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0],
      (player.play as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0],
    ]
    expect(order[0]).toBeLessThan(order[1])
  })

  it('never starts below zero', () => {
    const player = fakePlayer()
    render(player, 2).skipBy(-5)
    expect(player.play).toHaveBeenCalledWith(track, { startAt: 0 })
  })
})
