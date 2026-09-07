import { describe, it, expect, beforeEach } from 'vitest'
import {
  clampRateToAvailable,
  formatRateLabel,
  PLAYER_RATE_STORAGE_KEY,
  readPersistedRate,
  writePersistedRate,
} from './playbackRate'

describe('playbackRate helpers (spec #72 §5)', () => {
  beforeEach(() => localStorage.clear())

  it('round-trips a valid rate and defaults on garbage', () => {
    expect(readPersistedRate()).toBe(1)
    writePersistedRate(1.5)
    expect(localStorage.getItem(PLAYER_RATE_STORAGE_KEY)).toBe('1.5')
    expect(readPersistedRate()).toBe(1.5)
    localStorage.setItem(PLAYER_RATE_STORAGE_KEY, 'NaN')
    expect(readPersistedRate()).toBe(1)
    localStorage.setItem(PLAYER_RATE_STORAGE_KEY, '9')
    expect(readPersistedRate()).toBe(1)
  })

  it('refuses to persist an out-of-range rate', () => {
    writePersistedRate(0)
    writePersistedRate(Infinity)
    expect(localStorage.getItem(PLAYER_RATE_STORAGE_KEY)).toBeNull()
  })

  it('clamps to the nearest accepted rate and passes through when unrestricted', () => {
    const list = [0.5, 1, 1.25, 1.5, 2]
    expect(clampRateToAvailable(1.2, list)).toBe(1.25)
    expect(clampRateToAvailable(0.8, list)).toBe(1)
    expect(clampRateToAvailable(3, list)).toBe(2)
    expect(clampRateToAvailable(1.2, null)).toBe(1.2)
    expect(clampRateToAvailable(1.2, [])).toBe(1.2)
  })

  it('formats labels without trailing zeros', () => {
    expect(formatRateLabel(1)).toBe('1×')
    expect(formatRateLabel(1.5)).toBe('1.5×')
    expect(formatRateLabel(0.8)).toBe('0.8×')
    expect(formatRateLabel(1.25)).toBe('1.25×')
  })
})
