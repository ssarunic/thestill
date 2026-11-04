import { useCallback, useMemo } from 'react'
import type { PlayerContextValue, PlayerTrack } from '../../contexts/PlayerContext'

// Spec #110 §5.5–5.6 — the editor's one playback source. Every transport
// gesture (dock buttons, J/K/L, word clicks, lane clicks) goes through
// here, so they all act on the episode being edited and on the file the
// timestamps were made from, never on whatever the global player happens
// to hold: another episode, or this one on its publisher URL or on the
// YouTube engine (spec #62 — ``play()`` keeps the engine for a same-episode
// call, so the editor leaves it explicitly first).

export const SKIP_SECONDS = 5

export type EditorPlaybackPlayer = Pick<
  PlayerContextValue,
  | 'track'
  | 'isPlaying'
  | 'activeEngine'
  | 'duration'
  | 'play'
  | 'pause'
  | 'resume'
  | 'toggle'
  | 'seek'
  | 'skip'
  | 'switchRendition'
  | 'isCurrent'
  | 'getCurrentTime'
>

export interface EditorPlaybackArgs {
  // The episode's track with ``audioUrl`` already pointed at the local
  // copy when one exists (null while the episode is still loading).
  desiredTrack: PlayerTrack | null
  player: EditorPlaybackPlayer
  // ``usePlayerTime()`` — the subscribed clock, so skips from a foreign
  // source start where the dock shows the playhead.
  currentTime: number
  // Where an unplayed episode starts: the selected row's start (already
  // offset onto the asset's timeline), else the top.
  fallbackStart: number
  episodeDuration: number
}

export interface EditorPlayback {
  isCurrent: boolean
  // The global player holds this episode on the editor's source.
  onOurSource: boolean
  duration: number
  seekTo: (seconds: number) => void
  togglePlay: () => void
  skipBy: (deltaSeconds: number) => void
  pause: () => void
}

export function useEditorPlayback({
  desiredTrack,
  player,
  currentTime,
  fallbackStart,
  episodeDuration,
}: EditorPlaybackArgs): EditorPlayback {
  const isCurrent = desiredTrack ? player.isCurrent(desiredTrack.episodeId) : false
  const onOurSource = isCurrent && player.track?.audioUrl === desiredTrack?.audioUrl

  const playOurSource = useCallback(
    (startAt: number) => {
      if (!desiredTrack) return
      // Same episode on the YouTube iframe: play() would seek the iframe
      // and keep it; step back to the native engine so the local file is
      // what the timestamps are applied to.
      if (isCurrent && player.activeEngine === 'youtube') player.switchRendition('audio')
      player.play(desiredTrack, { startAt: Math.max(0, startAt) })
    },
    [desiredTrack, isCurrent, player],
  )

  const seekTo = useCallback(
    (seconds: number) => {
      if (!desiredTrack) return
      if (onOurSource) {
        player.seek(seconds)
        if (!player.isPlaying) player.resume()
        return
      }
      playOurSource(seconds)
    },
    [desiredTrack, onOurSource, player, playOurSource],
  )

  const togglePlay = useCallback(() => {
    if (!desiredTrack) return
    if (onOurSource) player.toggle()
    else playOurSource(isCurrent ? player.getCurrentTime() : fallbackStart)
  }, [desiredTrack, onOurSource, isCurrent, player, playOurSource, fallbackStart])

  const skipBy = useCallback(
    (delta: number) => {
      if (!desiredTrack) return
      if (onOurSource) player.skip(delta)
      else playOurSource((isCurrent ? currentTime : 0) + delta)
    },
    [desiredTrack, onOurSource, isCurrent, currentTime, player, playOurSource],
  )

  // Pausing is the one gesture that must not start anything: K on a foreign
  // episode leaves it alone.
  const pause = useCallback(() => {
    if (isCurrent && player.isPlaying) player.pause()
  }, [isCurrent, player])

  const duration = onOurSource && player.duration > 0 ? player.duration : episodeDuration

  return useMemo(
    () => ({ isCurrent, onOurSource, duration, seekTo, togglePlay, skipBy, pause }),
    [isCurrent, onOurSource, duration, seekTo, togglePlay, skipBy, pause],
  )
}
