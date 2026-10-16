import { describe, expect, it } from 'vitest'
import type { ListeningSummary } from '../api/types'
import { listeningState, progressFraction, timeLeftLabel } from './listeningFormat'

const progress = (position: number, overrides: Partial<ListeningSummary> = {}): ListeningSummary => ({
  position_seconds: position,
  duration_seconds: 3600,
  completed_at: null,
  updated_at: '2026-10-16T09:00:00Z',
  ...overrides,
})

describe('listeningFormat (spec #90)', () => {
  it('derives the state', () => {
    expect(listeningState(null)).toBe('none')
    expect(listeningState(progress(0))).toBe('none')
    expect(listeningState(progress(1680))).toBe('in_progress')
    expect(listeningState(progress(0, { completed_at: '2026-10-16T09:00:00Z' }))).toBe('played')
  })

  it('labels the time left from the row duration, then the feed fallback', () => {
    expect(timeLeftLabel(progress(1680))).toBe('32 min left')
    expect(timeLeftLabel(progress(3590))).toBe('1 min left')
    expect(timeLeftLabel(progress(600, { duration_seconds: null }), 1200)).toBe('10 min left')
    expect(timeLeftLabel(progress(600, { duration_seconds: null }))).toBeNull()
    expect(timeLeftLabel(progress(0, { completed_at: '2026-10-16T09:00:00Z' }))).toBeNull()
  })

  it('reports the share listened while in progress', () => {
    expect(progressFraction(progress(900))).toBe(0.25)
    expect(progressFraction(progress(9000))).toBe(1)
    expect(progressFraction(null)).toBeNull()
  })
})
