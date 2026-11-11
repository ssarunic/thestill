// Spec #116 — where drift alignment is allowed to run at all.
//
// The whole chain (service worker relaying media, `crossorigin` loading,
// the held start, the applied map) is switched on per browser, once it has
// been verified end to end there:
//
// - desktop Chrome and Edge;
// - desktop Safari, checked by hand in Safari 27.2 on 2026-11-11. Its first
//   release there (v1.42.0) restarted playback from 0 on every seek: Safari
//   cannot play audio muted, which is how the wait for the map was spent.
//   The wait is now spent paused (see NativeEngine.beginHold).
//
// Every other browser gets none of it and plays exactly as it did before
// the feature. Not yet checked: iPhone and iPad, Firefox, Android.
//
// A user-agent check is the right tool here. This is not feature
// detection (Safari has every API involved) but a statement about where
// the behaviour has been proven.

const CHROMIUM = /\b(?:Chrome|Chromium)\/\d+/
const NOT_DESKTOP = /\b(?:Mobile|Android|iPhone|iPad|iPod|CriOS|FxiOS|EdgiOS)\b/
const MAC_SAFARI = /\(Macintosh;.*\bVersion\/\d+.*\bSafari\/\d+/

// Build-time override, for trying a browser that is not yet on the list
// against a local build (`VITE_DRIFT_ANY_BROWSER=1 npm run build`). Never
// set for a release build.
const ANY_BROWSER = import.meta.env.VITE_DRIFT_ANY_BROWSER === '1'

/**
 * `touchPoints` tells an iPad from a Mac: an iPad asking for the desktop
 * site sends a Mac's user agent, and only a Mac has no touch screen.
 */
export function driftAlignmentSupported(userAgent?: string, touchPoints?: number): boolean {
  if (ANY_BROWSER && userAgent === undefined) return true
  const nav = typeof navigator === 'undefined' ? null : navigator
  const ua = userAgent ?? nav?.userAgent ?? ''
  if (NOT_DESKTOP.test(ua)) return false
  if (CHROMIUM.test(ua)) return true
  const touch = touchPoints ?? (userAgent === undefined ? (nav?.maxTouchPoints ?? 0) : 0)
  return MAC_SAFARI.test(ua) && touch === 0
}
