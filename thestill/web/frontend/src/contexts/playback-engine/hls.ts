// Spec #113 — HLS support for the native engine.
//
// Safari (and any browser whose canPlayType reports native HLS) plays an
// .m3u8 straight from the stable <video>'s src. Everywhere else hls.js
// drives the same element through Media Source Extensions. hls.js is a
// lazy chunk — only HLS episodes in browsers without native HLS ever load
// it — and its transmux worker is a same-origin asset, so the CSP needs no
// blob: worker-src.

import type Hls from 'hls.js'

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

/** True when this source has to go through hls.js on this element. */
export function needsHlsJs(el: HTMLMediaElement, url: string, mimeType?: string | null): boolean {
  return isHlsSource(url, mimeType) && el.canPlayType('application/vnd.apple.mpegurl') === ''
}

let loaded: HlsModule | null = null
let pending: Promise<HlsModule> | null = null

/** The hls.js module once it has loaded, else null (synchronous). */
export function loadedHls(): HlsModule | null {
  return loaded
}

/**
 * Load hls.js (light build: no subtitles, alternate audio or DRM — none
 * are used) plus the URL of its worker. Memoised; a failed chunk load is
 * forgotten so the next attempt retries.
 */
export function loadHls(): Promise<HlsModule> {
  if (!pending) {
    pending = Promise.all([import('hls.js/light'), import('hls.js/dist/hls.worker.js?url')]).then(
      ([mod, worker]) => {
        loaded = { Hls: mod.default, workerPath: worker.default }
        return loaded
      },
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
