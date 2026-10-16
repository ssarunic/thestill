// Spec #90 "Tab snapshot" — the player session mirrored into
// sessionStorage so a reload of the same tab restores without waiting for
// the network. sessionStorage, not localStorage: whether *this tab* was
// playing and which rendition it chose are tab facts — a new tab must not
// try to resume another tab's session (it asks the server instead).
// Every access is guarded: blocked storage (private mode) falls back to the
// server path.
import type { ProgressBody } from '../../api/types'
import type { PlayerTrack, RenditionKind } from '../PlayerContext'

export const PLAYER_SESSION_STORAGE_KEY = 'thestill:player:session'
const VERSION = 2

// The owner key of a player rendered without an AuthProvider (isolated tests).
export const LOCAL_USER_KEY = 'local'

// A write the server has not acknowledged yet, re-sent with its original
// session_id and seq so server-side ordering still holds.
export interface PendingWrite {
  episodeId: string
  body: ProgressBody
}

export interface SessionSnapshot {
  v: typeof VERSION
  // Whose session this is: a snapshot is only ever restored by its owner.
  userKey: string
  track: PlayerTrack
  // Logical (transcript-timeline) position, seconds.
  logicalPosition: number
  wasPlaying: boolean
  rendition: RenditionKind
  pending: PendingWrite | null
  // Epoch ms of the last mirror.
  savedAt: number
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function isSnapshot(value: unknown): value is SessionSnapshot {
  if (!value || typeof value !== 'object') return false
  const s = value as Partial<SessionSnapshot>
  return (
    s.v === VERSION &&
    typeof s.userKey === 'string' &&
    typeof s.savedAt === 'number' &&
    typeof s.logicalPosition === 'number' &&
    Number.isFinite(s.logicalPosition) &&
    typeof s.wasPlaying === 'boolean' &&
    (s.rendition === 'audio' || s.rendition === 'video') &&
    typeof s.track?.episodeId === 'string' &&
    typeof s.track?.audioUrl === 'string'
  )
}

/** The tab's snapshot when it belongs to `userKey`, else null. */
export function readSnapshot(userKey: string): SessionSnapshot | null {
  const store = storage()
  if (!store) return null
  try {
    const raw = store.getItem(PLAYER_SESSION_STORAGE_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return isSnapshot(parsed) && parsed.userKey === userKey ? parsed : null
  } catch {
    return null
  }
}

export function writeSnapshot(userKey: string, snapshot: Omit<SessionSnapshot, 'v' | 'userKey'>): void {
  const store = storage()
  if (!store) return
  try {
    store.setItem(PLAYER_SESSION_STORAGE_KEY, JSON.stringify({ v: VERSION, userKey, ...snapshot }))
  } catch {
    // Quota or private mode — the server path still covers restore.
  }
}

export function clearSnapshot(): void {
  const store = storage()
  if (!store) return
  try {
    store.removeItem(PLAYER_SESSION_STORAGE_KEY)
  } catch {
    // Nothing to clear.
  }
}
