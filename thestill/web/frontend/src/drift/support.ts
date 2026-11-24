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
// The exception is a session that holds `drift.align.unverified` (admins,
// while the `drift_unverified_browsers` runtime setting is `admins`): it
// gets the feature in every browser, and its plays there are the check that
// puts a browser on the list. `thestill settings set
// drift_unverified_browsers off` takes that away without a deploy.
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

const UNVERIFIED_KEY = 'thestill:drift:unverified'

function rememberedUnverified(): boolean {
  try {
    return localStorage.getItem(UNVERIFIED_KEY) === '1'
  } catch {
    return false
  }
}

// The permission is known only once the session has loaded, and the player
// restores a saved source before that (see corsVerdict.ts). So the last
// known answer is kept across page loads, like the feature's own switch.
let unverifiedAllowed = rememberedUnverified()

/** Whether this session may run the feature in a browser that is not on the
 * list (it holds `drift.align.unverified`). Remembered for the next load. */
export function setUnverifiedBrowsersAllowed(on: boolean): void {
  unverifiedAllowed = on
  try {
    if (on) localStorage.setItem(UNVERIFIED_KEY, '1')
    else localStorage.removeItem(UNVERIFIED_KEY)
  } catch {
    // No storage: the answer holds for this page only.
  }
}

/**
 * Whether the browser is on the list. `touchPoints` tells an iPad from a
 * Mac: an iPad asking for the desktop site sends a Mac's user agent, and
 * only a Mac has no touch screen.
 */
export function driftBrowserVerified(userAgent?: string, touchPoints?: number): boolean {
  if (ANY_BROWSER && userAgent === undefined) return true
  const nav = typeof navigator === 'undefined' ? null : navigator
  const ua = userAgent ?? nav?.userAgent ?? ''
  if (NOT_DESKTOP.test(ua)) return false
  if (CHROMIUM.test(ua)) return true
  const touch = touchPoints ?? (userAgent === undefined ? (nav?.maxTouchPoints ?? 0) : 0)
  return MAC_SAFARI.test(ua) && touch === 0
}

/** Whether the feature may run in this browser, for a session that holds
 * `drift.align`: the browser is on the list, or the session was last seen
 * to hold `drift.align.unverified`. */
export function driftAlignmentSupported(): boolean {
  return unverifiedAllowed || driftBrowserVerified()
}
