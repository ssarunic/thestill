// Spec #71 — layering ladder for fixed surfaces. The mini player is shell
// chrome (the bottom edge of the app, as the sidebar is the left edge):
// long-lived surfaces lay out *inside* the shell, transient surfaces cover
// it. Every fixed element picks a rung from this table rather than an ad-hoc
// z-index:
//
//   z-0..20   page content, sticky rails, in-panel floating pills
//   z-40      shell: sidebar, mobile header, floating video tile, bulk bar
//   z-[45]    reader overlay (spec #52) — long-lived; insets above the player
//   z-50      mini player — persistent shell chrome
//   z-[52]/55 tablet sidebar scrim/expanded sidebar — transient, never coexists
//             with the reader overlay (its hamburger sits under the scrim)
//   60        media layer when its slot is inside the reader (PlayerContext)
//   z-[70]    transient modals: ⌘K, add/import, failure details, nav drawer,
//             toasts, the Now Playing sheet — short-lived, may cover the player
//   71        media layer when its slot is inside the Now Playing sheet
//             (spec #72 phase 2c) — resolved by `mediaLayerZIndex`
//
// The player publishes its rendered height as a CSS custom property on the
// document root so every bottom-anchored surface can clear it without
// hard-coding the bar's size (0px when nothing is loaded).

export const PLAYER_HEIGHT_VAR = '--player-h'

/** `bottom` value that clears the mini player by `gap` (a CSS length). */
export function abovePlayer(gap = '0px'): string {
  return gap === '0px' ? `var(${PLAYER_HEIGHT_VAR}, 0px)` : `calc(var(${PLAYER_HEIGHT_VAR}, 0px) + ${gap})`
}

/** Height of the fixed mobile shell header (``MobileHeader``, ``h-14``); ``Layout`` pads ``main`` by it below ``sm``. */
export const MOBILE_HEADER_HEIGHT = 56

/**
 * Marker a fixed host sets so the media layer (the one stable `<video>` /
 * iframe node, spec #61 invariant 2) knows which rung to paint over when a
 * presentation slot is registered inside it. Only the Now Playing sheet
 * needs one today; the reader overlay is recognised by its `role="dialog"`.
 */
export const MEDIA_HOST_ATTR = 'data-media-host'
export type MediaHost = 'now-playing'

/**
 * z-index for the media layer given the element its slot lives in: one rung
 * above whatever fixed surface hosts the slot, so the video is never painted
 * behind its own host. Shell rung (40) when the slot is in page content.
 */
export function mediaLayerZIndex(slot: Element): number {
  if (slot.closest(`[${MEDIA_HOST_ATTR}="now-playing"]`)) return 71
  if (slot.closest('[role="dialog"]')) return 60
  return 40
}
