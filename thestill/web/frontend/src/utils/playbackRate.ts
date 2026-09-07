// Spec #72 §5 — playback-rate preference: one global value, persisted in
// localStorage, applied by PlayerProvider on every new track / rendition
// switch / YouTube entry so it survives reloads and engine switches.
// Pure helpers only; the React side lives in hooks/usePlayerRatePreference.

export const RATE_OPTIONS: readonly number[] = [0.8, 1, 1.2, 1.5, 2]
export const PLAYER_RATE_STORAGE_KEY = 'thestill:player:rate'
export const DEFAULT_RATE = 1

// Sanity bounds: what both engines accept without complaint.
const MIN_RATE = 0.25
const MAX_RATE = 4

export function isValidRate(rate: unknown): rate is number {
  return typeof rate === 'number' && Number.isFinite(rate) && rate >= MIN_RATE && rate <= MAX_RATE
}

export function readPersistedRate(): number {
  if (typeof window === 'undefined') return DEFAULT_RATE
  try {
    const raw = window.localStorage.getItem(PLAYER_RATE_STORAGE_KEY)
    if (raw === null) return DEFAULT_RATE
    const parsed = Number(raw)
    return isValidRate(parsed) ? parsed : DEFAULT_RATE
  } catch {
    return DEFAULT_RATE
  }
}

export function writePersistedRate(rate: number): void {
  if (typeof window === 'undefined' || !isValidRate(rate)) return
  try {
    window.localStorage.setItem(PLAYER_RATE_STORAGE_KEY, String(rate))
  } catch {
    // Safari private mode rejects writes; the in-memory preference still applies.
  }
}

/**
 * Nearest rate the current engine accepts. `available` is null when the
 * engine imposes no restriction (native media) or has not reported yet.
 */
export function clampRateToAvailable(rate: number, available: readonly number[] | null): number {
  if (!available || available.length === 0) return rate
  let best = available[0]
  for (const candidate of available) {
    if (Math.abs(candidate - rate) < Math.abs(best - rate)) best = candidate
  }
  return best
}

/** `1×`, `1.5×`, `0.8×` — trailing zeros dropped. */
export function formatRateLabel(rate: number): string {
  return `${Number(rate.toFixed(2))}×`
}
