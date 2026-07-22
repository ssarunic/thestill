// Spec #62 §5 — lazy, idempotent loader for the YouTube IFrame Player API.
//
// The script tag is injected only on first YouTube playback (episodes that
// never touch the rendition pay zero cost). YouTube's bootstrap contract
// is the global `onYouTubeIframeAPIReady` callback; a prior consumer's
// callback is chained rather than clobbered. Ambient types are declared
// locally — no @types/youtube dependency.

export interface YTPlayerEvent {
  data: number
  target: YTPlayer
}

export interface YTPlayer {
  playVideo(): void
  pauseVideo(): void
  seekTo(seconds: number, allowSeekAhead: boolean): void
  setPlaybackRate(rate: number): void
  getPlaybackRate(): number
  getCurrentTime(): number
  getDuration(): number
  getPlayerState(): number
  setVolume(volume: number): void
  mute(): void
  unMute(): void
  loadVideoById(videoId: string, startSeconds?: number): void
  destroy(): void
}

export interface YTNamespace {
  Player: new (
    el: HTMLElement,
    opts: {
      videoId?: string
      playerVars?: Record<string, string | number>
      events?: {
        onReady?: (e: YTPlayerEvent) => void
        onStateChange?: (e: YTPlayerEvent) => void
        onError?: (e: YTPlayerEvent) => void
        onPlaybackRateChange?: (e: YTPlayerEvent) => void
      }
    },
  ) => YTPlayer
  PlayerState: {
    UNSTARTED: number
    ENDED: number
    PLAYING: number
    PAUSED: number
    BUFFERING: number
    CUED: number
  }
}

declare global {
  interface Window {
    YT?: YTNamespace
    onYouTubeIframeAPIReady?: () => void
  }
}

let apiPromise: Promise<YTNamespace> | null = null

export function loadYouTubeIframeApi(): Promise<YTNamespace> {
  if (apiPromise) return apiPromise
  apiPromise = new Promise<YTNamespace>((resolve, reject) => {
    if (window.YT?.Player) {
      resolve(window.YT)
      return
    }
    const prior = window.onYouTubeIframeAPIReady
    window.onYouTubeIframeAPIReady = () => {
      prior?.()
      if (window.YT?.Player) resolve(window.YT)
      else reject(new Error('YouTube IFrame API loaded without YT.Player'))
    }
    const script = document.createElement('script')
    script.src = 'https://www.youtube.com/iframe_api'
    script.async = true
    script.onerror = () => {
      // Allow a retry on the next user attempt instead of caching failure
      // forever (e.g. a transient network drop or content blocker toggle).
      apiPromise = null
      reject(new Error('Failed to load the YouTube player.'))
    }
    document.head.appendChild(script)
  })
  return apiPromise
}
