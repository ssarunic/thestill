// Spec #61 §2 — Media Session API integration (lock-screen artwork,
// play/pause/±15 s, position state). Ships regardless of video and benefits
// audio episodes. Every call is feature-detected and wrapped in try/catch:
// `setActionHandler` support varies per action and per browser, and the spec
// treats browser state as authoritative — a failed call must never break
// playback.

export interface MediaSessionTrackInfo {
  title: string
  podcastTitle?: string | null
  artworkUrl?: string | null
}

export function supportsMediaSession(): boolean {
  return typeof navigator !== 'undefined' && 'mediaSession' in navigator
}

export function setMediaSessionMetadata(info: MediaSessionTrackInfo | null): void {
  if (!supportsMediaSession()) return
  try {
    if (info === null) {
      navigator.mediaSession.metadata = null
      return
    }
    if (typeof MediaMetadata !== 'function') return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: info.title,
      artist: info.podcastTitle ?? '',
      artwork: info.artworkUrl
        ? [{ src: info.artworkUrl, sizes: '512x512' }]
        : [],
    })
  } catch {
    // Metadata is a nicety; never let it interfere with playback.
  }
}

export function setMediaSessionPlaybackState(state: 'playing' | 'paused' | 'none'): void {
  if (!supportsMediaSession()) return
  try {
    navigator.mediaSession.playbackState = state
  } catch {
    // Ignore — optional enhancement.
  }
}

/**
 * Register handlers per-action. Each `setActionHandler` call gets its own
 * try/catch because browsers throw `TypeError` for actions they don't
 * support — one unsupported action must not disable the rest.
 */
export function setMediaSessionActionHandlers(
  handlers: Partial<Record<MediaSessionAction, MediaSessionActionHandler | null>>,
): void {
  if (!supportsMediaSession()) return
  if (typeof navigator.mediaSession.setActionHandler !== 'function') return
  for (const [action, handler] of Object.entries(handlers)) {
    try {
      navigator.mediaSession.setActionHandler(action as MediaSessionAction, handler ?? null)
    } catch {
      // Action not supported by this browser — skip it.
    }
  }
}

export function updateMediaSessionPositionState(
  duration: number,
  playbackRate: number,
  position: number,
): void {
  if (!supportsMediaSession()) return
  try {
    if (typeof navigator.mediaSession.setPositionState !== 'function') return
    if (!Number.isFinite(duration) || duration <= 0) return
    navigator.mediaSession.setPositionState({
      duration,
      playbackRate: Number.isFinite(playbackRate) && playbackRate > 0 ? playbackRate : 1,
      position: Math.min(Math.max(0, position), duration),
    })
  } catch {
    // Ignore — optional enhancement.
  }
}
