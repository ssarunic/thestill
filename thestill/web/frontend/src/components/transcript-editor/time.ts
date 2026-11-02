// Spec #110 §5 — clock formatting for the editor's gutter, dock and lanes.

/** ``m:ss`` under an hour, ``h:mm:ss`` above; ``fraction`` adds tenths. */
export function formatClock(seconds: number, fraction = false): string {
  const total = Math.max(0, seconds)
  const whole = Math.floor(total)
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = whole % 60
  const pad = (n: number) => n.toString().padStart(2, '0')
  const base = h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
  if (!fraction) return base
  // Floor on a scaled integer: 30.2 − 30 is 0.1999… in binary and would read as .1.
  const tenths = Math.floor(total * 10 + 1e-6) % 10
  return `${base}.${tenths}`
}
