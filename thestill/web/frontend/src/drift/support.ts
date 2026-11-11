// Spec #116 — where drift alignment is allowed to run at all.
//
// The whole chain (service worker relaying media, `crossorigin` loading,
// the held start, the applied map) has been verified end to end only in
// desktop Chromium. In Safari, released to admins on 2026-11-11, playback
// restarted from the beginning on every seek. So this is an allow-list: a
// browser gets the feature once it has been tested there, and every other
// browser gets none of it and plays exactly as it did before the feature.
//
// A user-agent check is the right tool here. This is not feature
// detection (Safari has every API involved) but a statement about where
// the behaviour has been proven.

const CHROMIUM = /\b(?:Chrome|Chromium)\/\d+/
const NOT_DESKTOP = /\b(?:Mobile|Android|iPhone|iPad|iPod|CriOS|FxiOS|EdgiOS)\b/

// Build-time override, for trying a browser that is not yet on the list
// against a local build (`VITE_DRIFT_ANY_BROWSER=1 npm run build`). Never
// set for a release build.
const ANY_BROWSER = import.meta.env.VITE_DRIFT_ANY_BROWSER === '1'

export function driftAlignmentSupported(userAgent?: string): boolean {
  if (ANY_BROWSER && userAgent === undefined) return true
  const ua = userAgent ?? (typeof navigator === 'undefined' ? '' : navigator.userAgent)
  return CHROMIUM.test(ua) && !NOT_DESKTOP.test(ua)
}
