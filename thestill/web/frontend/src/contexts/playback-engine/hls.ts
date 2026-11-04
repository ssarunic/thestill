// Spec #113 — HLS support for the native engine.
//
// Safari (and any browser whose canPlayType reports native HLS) plays an
// .m3u8 straight from the stable <video>'s src. Everywhere else hls.js
// drives the same element through Media Source Extensions. hls.js is a
// lazy chunk — only HLS episodes in browsers without native HLS ever load
// it — and its transmux worker is a same-origin asset, so the CSP needs no
// blob: worker-src.

import type Hls from 'hls.js'
import type { Events } from 'hls.js'

export type HlsConstructor = typeof Hls
export type HlsInstance = Hls

export interface HlsModule {
  Hls: HlsConstructor
  workerPath: string
}

const HLS_MIME_TYPES = new Set(['application/x-mpegurl', 'application/vnd.apple.mpegurl'])

/** True when the source is an HLS playlist: by MIME type, else by `.m3u8` path. */
export function isHlsSource(url: string, mimeType?: string | null): boolean {
  if (mimeType) return HLS_MIME_TYPES.has(mimeType.toLowerCase())
  try {
    return new URL(url, 'https://invalid.local').pathname.toLowerCase().endsWith('.m3u8')
  } catch {
    return false
  }
}

export function canPlayHlsNatively(el: HTMLMediaElement): boolean {
  return el.canPlayType('application/vnd.apple.mpegurl') !== ''
}

function hasMediaSource(): boolean {
  return typeof window !== 'undefined' && ('MediaSource' in window || 'ManagedMediaSource' in window)
}

// Safari and every iOS browser. Their native HLS is first-class (AirPlay,
// lock screen, background audio); this AirPlay event exists nowhere else.
function isAppleWebKit(): boolean {
  return typeof window !== 'undefined' && 'WebKitPlaybackTargetAvailabilityEvent' in window
}

/**
 * True when this source has to go through hls.js on this element.
 *
 * `canPlayType` alone cannot decide: Chromium reports native HLS support
 * ('maybe', Chrome 156) yet its built-in HLS fails every seek on Flightcast
 * VOD streams (DEMUXER_ERROR_COULD_NOT_PARSE, 2026-11-04) while hls.js
 * seeks them fine. So hls.js wherever Media Source Extensions exist, except
 * on Apple WebKit; native only there, or where MSE is missing.
 */
export function needsHlsJs(el: HTMLMediaElement, url: string, mimeType?: string | null): boolean {
  if (!isHlsSource(url, mimeType)) return false
  const native = canPlayHlsNatively(el)
  if (native && (isAppleWebKit() || !hasMediaSource())) return false
  return true
}

// hls.js's Events.ERROR value as a literal, so an engine can move a
// listener between instances without the lazily loaded module at hand.
export const HLS_ERROR_EVENT = 'hlsError' as Events.ERROR

let pending: Promise<HlsModule> | null = null

/**
 * Load hls.js (light build: no subtitles, alternate audio or DRM — none
 * are used) plus the URL of its worker. Memoised; a failed chunk load is
 * forgotten so the next attempt retries.
 */
export function loadHls(): Promise<HlsModule> {
  if (!pending) {
    pending = Promise.all([import('hls.js/light'), import('hls.js/dist/hls.worker.js?url')]).then(
      ([mod, worker]) => ({ Hls: mod.default, workerPath: worker.default }),
      (err: unknown) => {
        pending = null
        throw err
      },
    )
  }
  return pending
}

/** Warm the chunk so the first play click finds hls.js cached. */
export function preloadHls(): void {
  loadHls().catch(() => {})
}
