// Spec #90 — pure resume/restore rules, the tab snapshot and the cache.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ListeningSummary } from '../../api/types'
import { listeningCache } from './listeningCache'
import { AUTOPLAY_MAX_AGE_MS, SNAPSHOT_MAX_AGE_MS, planRestore } from './restore'
import { resolveResume, resumablePosition } from './resume'
import {
  PLAYER_SESSION_STORAGE_KEY,
  clearSnapshot,
  readSnapshot,
  writeSnapshot,
  type SessionSnapshot,
} from './sessionSnapshot'

const summary = (overrides: Partial<ListeningSummary> = {}): ListeningSummary => ({
  position_seconds: 1934,
  duration_seconds: 7412,
  completed_at: null,
  updated_at: '2026-10-16T09:00:00Z',
  ...overrides,
})

describe('resolveResume', () => {
  it('an explicit startAt wins, verbatim (engine time)', () => {
    expect(resolveResume({ startAt: 30, cached: summary(), offset: 2 })).toEqual({ seekTo: 30, fetchLate: false })
  })

  it('maps a stored logical position onto the asset timeline', () => {
    expect(resolveResume({ cached: summary({ position_seconds: 100 }), offset: 12 })).toEqual({
      seekTo: 112,
      fetchLate: false,
    })
  })

  it('restart, completed, never played and near-zero positions start at the top', () => {
    expect(resolveResume({ restart: true, cached: summary(), offset: 0 }).seekTo).toBeNull()
    expect(resolveResume({ cached: summary({ completed_at: '2026-10-16T09:00:00Z' }), offset: 0 }).seekTo).toBeNull()
    expect(resolveResume({ cached: null, offset: 0 })).toEqual({ seekTo: null, fetchLate: false })
    expect(resumablePosition(summary({ position_seconds: 1 }))).toBeNull()
  })

  it('an unknown position is fetched late', () => {
    expect(resolveResume({ cached: undefined, offset: 0 })).toEqual({ seekTo: null, fetchLate: true })
  })
})

describe('planRestore', () => {
  const now = Date.parse('2026-10-16T09:00:00Z')
  const snap = (ageMs: number, wasPlaying = true) =>
    ({ savedAt: now - ageMs, wasPlaying }) as SessionSnapshot

  it('no snapshot, or one 30 minutes old, falls back to the server', () => {
    expect(planRestore(null, now)).toEqual({ kind: 'latest' })
    expect(planRestore(snap(SNAPSHOT_MAX_AGE_MS), now)).toEqual({ kind: 'latest' })
  })

  it('autoplays only a snapshot of a playing tab under 2 minutes old', () => {
    expect(planRestore(snap(10_000), now)).toMatchObject({ kind: 'snapshot', autoplay: true })
    expect(planRestore(snap(AUTOPLAY_MAX_AGE_MS), now)).toMatchObject({ kind: 'snapshot', autoplay: false })
    expect(planRestore(snap(10_000, false), now)).toMatchObject({ kind: 'snapshot', autoplay: false })
  })
})

describe('session snapshot', () => {
  const fields: Omit<SessionSnapshot, 'v'> = {
    track: { episodeId: 'ep', podcastSlug: 'p', episodeSlug: 'e', title: 'T', audioUrl: 'https://x/a.mp3' },
    logicalPosition: 42,
    wasPlaying: true,
    rendition: 'audio',
    pending: null,
    savedAt: 1,
  }

  beforeEach(() => sessionStorage.clear())
  afterEach(() => vi.restoreAllMocks())

  it('round-trips and clears', () => {
    writeSnapshot('u1', fields)
    expect(readSnapshot('u1')).toEqual({ v: 2, userKey: 'u1', ...fields })
    clearSnapshot()
    expect(readSnapshot('u1')).toBeNull()
  })

  it('is read back only by the user who wrote it', () => {
    writeSnapshot('alice', fields)
    expect(readSnapshot('bob')).toBeNull()
    expect(readSnapshot('alice')).not.toBeNull()
  })

  it('ignores malformed or foreign values', () => {
    sessionStorage.setItem(PLAYER_SESSION_STORAGE_KEY, '{not json')
    expect(readSnapshot('u1')).toBeNull()
    sessionStorage.setItem(PLAYER_SESSION_STORAGE_KEY, JSON.stringify({ v: 2, userKey: 'u1', track: {} }))
    expect(readSnapshot('u1')).toBeNull()
  })

  it('blocked storage reads as empty and writes are dropped', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })
    expect(() => writeSnapshot('u1', fields)).not.toThrow()
    expect(readSnapshot('u1')).toBeNull()
  })
})

describe('listeningCache', () => {
  beforeEach(() => listeningCache.clear())

  it('keeps the later server stamp and notifies only that episode', () => {
    const a = vi.fn()
    const b = vi.fn()
    listeningCache.subscribe('a', a)
    listeningCache.subscribe('b', b)

    listeningCache.seed('a', summary({ position_seconds: 10, updated_at: '2026-10-16T09:00:10Z' }))
    listeningCache.seed('a', summary({ position_seconds: 5, updated_at: '2026-10-16T09:00:05Z' }))
    expect(listeningCache.get('a')?.position_seconds).toBe(10)
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).not.toHaveBeenCalled()
  })

  it('"never played" fills only an unknown entry', () => {
    listeningCache.seed('a', null)
    expect(listeningCache.get('a')).toBeNull()
    listeningCache.seed('a', summary())
    listeningCache.seed('a', null)
    expect(listeningCache.get('a')?.position_seconds).toBe(1934)
    expect(listeningCache.get('unknown')).toBeUndefined()
  })
})
