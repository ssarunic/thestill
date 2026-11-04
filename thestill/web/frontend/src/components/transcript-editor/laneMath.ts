// Spec #110 §5.5 — the lane timeline's arithmetic, kept pure so it can be
// unit-tested without an SVG: which window of the episode is shown, where a
// second lands in pixels, how the window follows the playhead, and the tick
// spacing for the axis.

export const MIN_SPAN_SECONDS = 20
export const DEFAULT_SPAN_SECONDS = 120

/** The axis tick spacing that keeps ticks readable for ``span`` seconds on screen. */
export function tickStep(spanSeconds: number): number {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
  const target = spanSeconds / 8
  return steps.find((step) => step >= target) ?? 3600
}

export function timeToX(seconds: number, windowStart: number, spanSeconds: number, width: number): number {
  return ((seconds - windowStart) / spanSeconds) * width
}

export function xToTime(x: number, windowStart: number, spanSeconds: number, width: number): number {
  return windowStart + (x / width) * spanSeconds
}

/** Keep the window inside ``[0, duration]`` (a short episode shows from 0). */
export function clampWindow(start: number, spanSeconds: number, duration: number): number {
  const maxStart = Math.max(0, duration - spanSeconds)
  return Math.min(Math.max(0, start), maxStart)
}

/**
 * Where the window should start so ``currentTime`` stays visible while
 * playing: untouched while the playhead sits in the middle 70 % of the
 * window, otherwise re-anchored with the playhead at 30 % from the left.
 */
export function followWindow(currentTime: number, windowStart: number, spanSeconds: number, duration: number): number {
  const low = windowStart + spanSeconds * 0.15
  const high = windowStart + spanSeconds * 0.85
  if (currentTime >= low && currentTime <= high) return windowStart
  return clampWindow(currentTime - spanSeconds * 0.3, spanSeconds, duration)
}

/** Zoom around ``anchorTime`` by ``factor`` (> 1 zooms out). */
export function zoomWindow(
  windowStart: number,
  spanSeconds: number,
  factor: number,
  anchorTime: number,
  duration: number,
): { start: number; span: number } {
  const span = Math.min(Math.max(MIN_SPAN_SECONDS, spanSeconds * factor), Math.max(MIN_SPAN_SECONDS, duration))
  const ratio = spanSeconds > 0 ? (anchorTime - windowStart) / spanSeconds : 0.5
  const start = clampWindow(anchorTime - ratio * span, span, duration)
  return { start, span }
}

/** The tick times inside the window, on multiples of the step. */
export function ticksFor(windowStart: number, spanSeconds: number): number[] {
  const step = tickStep(spanSeconds)
  const first = Math.ceil(windowStart / step) * step
  const ticks: number[] = []
  for (let t = first; t <= windowStart + spanSeconds; t += step) ticks.push(t)
  return ticks
}

export interface FollowState {
  /** The user panned or zoomed since the window last followed. */
  panned: boolean
  /** The playhead was inside the window on the previous tick. */
  wasInside: boolean
}

export interface FollowDecision {
  start: number | null
  next: FollowState
}

/**
 * Where the window goes on a playback tick. Following keeps the playhead
 * in the middle of the window; after a manual pan or zoom it stays put
 * until the playhead, having been inside the chosen window, leaves it.
 * Panning away from the playhead therefore holds, and following resumes
 * once the user has caught up with it (or on a new selection).
 */
export function followDecision(
  currentTime: number,
  windowStart: number,
  spanSeconds: number,
  duration: number,
  state: FollowState,
): FollowDecision {
  const inside = currentTime >= windowStart && currentTime <= windowStart + spanSeconds
  if (!state.panned) {
    const start = followWindow(currentTime, windowStart, spanSeconds, duration)
    return { start: start === windowStart ? null : start, next: { panned: false, wasInside: inside } }
  }
  if (state.wasInside && !inside) {
    return {
      start: clampWindow(currentTime - spanSeconds * 0.3, spanSeconds, duration),
      next: { panned: false, wasInside: true },
    }
  }
  return { start: null, next: { panned: true, wasInside: inside } }
}
