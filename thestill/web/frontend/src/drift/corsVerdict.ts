// Spec #116 §4 step 6 — may the audio element load with
// `crossorigin="anonymous"`? The service worker can only read what the
// element receives when it does, and an element with the attribute fails
// to load from a host without CORS. So the answer is learned, per redirect
// chain, from the alignment's own fetch of the audio, remembered for a
// week, and applied from the next load. Playback never waits for it.

const KEY_PREFIX = 'thestill:drift:cors:'
const TTL_MS = 7 * 24 * 60 * 60 * 1000

export type CorsVerdict = 'cors_ok' | 'no_cors'

const HOST_IN_PATH = /^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i

/**
 * What the enclosure URL shows of its redirect chain: its own host and the
 * host names embedded in its path, which is how tracking prefixes name the
 * next hop (`dts.podtrac.com/redirect.mp3/traffic.megaphone.fm/…`). CORS
 * has to hold on every hop, so two shows behind the same chain share a
 * verdict and a show that moves host gets a new one.
 */
export function chainKey(url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
  const hosts = [parsed.hostname.toLowerCase()]
  for (const part of parsed.pathname.split('/')) {
    // File names such as `episode.mp3` look like hosts; an audio extension
    // gives them away.
    if (HOST_IN_PATH.test(part) && !/\.(mp3|m4a|aac|mp4|ogg|wav|m3u8)$/i.test(part)) hosts.push(part.toLowerCase())
  }
  return `${parsed.protocol}//${hosts.join('|')}`
}

export function getCorsVerdict(url: string, now = Date.now()): CorsVerdict | null {
  const key = chainKey(url)
  if (!key) return null
  try {
    const raw = localStorage.getItem(KEY_PREFIX + key)
    if (!raw) return null
    const stored = JSON.parse(raw) as { v?: unknown; t?: unknown }
    if ((stored.v !== 'cors_ok' && stored.v !== 'no_cors') || typeof stored.t !== 'number') return null
    if (now - stored.t > TTL_MS || stored.t > now + 60_000) return null
    return stored.v
  } catch {
    return null
  }
}

export function setCorsVerdict(url: string, verdict: CorsVerdict, now = Date.now()): void {
  const key = chainKey(url)
  if (!key) return
  try {
    localStorage.setItem(KEY_PREFIX + key, JSON.stringify({ v: verdict, t: now }))
  } catch {
    // Storage full or unavailable: the next play simply learns it again.
  }
}

// --- What the player does with it -----------------------------------------

let enabled = false

/** Switched on for sessions that hold `drift.align`; off, no element ever
 * gets the attribute. */
export function setCrossOriginLoadingEnabled(on: boolean): void {
  enabled = on
}

export interface AudioLoad {
  url: string
  crossOrigin: boolean
  /** `Date.now()` when the source was assigned. */
  t: number
}

let lastLoad: AudioLoad | null = null

/**
 * Called by the player as it assigns an audio source: should the element
 * carry `crossorigin`? Only for a chain already known to allow it.
 */
export function beginAudioLoad(url: string, eligible: boolean): boolean {
  const crossOrigin = enabled && eligible && getCorsVerdict(url) === 'cors_ok'
  lastLoad = { url, crossOrigin, t: Date.now() }
  return crossOrigin
}

/** The most recent audio load, for the verification of that play. */
export function currentAudioLoad(): AudioLoad | null {
  return lastLoad
}

/**
 * The element failed to load with the attribute: the chain stopped allowing
 * CORS (or never did on this hop). The engine reloads without it.
 */
export function rejectCrossOrigin(url: string): void {
  setCorsVerdict(url, 'no_cors')
  if (lastLoad && lastLoad.url === url) lastLoad = { ...lastLoad, crossOrigin: false }
}
