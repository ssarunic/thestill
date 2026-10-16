import { describe, it, expect } from 'vitest'
import { formatHours, formatPct, isSmallGroup, parseRange, sourceLabel } from './analyticsFormat'

describe('analyticsFormat (spec #95)', () => {
  it('parses only the allowed ranges', () => {
    expect(parseRange('7d')).toBe('7d')
    expect(parseRange('90d')).toBe('90d')
    expect(parseRange('1y')).toBe('28d')
    expect(parseRange(null)).toBe('28d')
  })

  it('formats percentages and missing values', () => {
    expect(formatPct(0.4567)).toBe('45.7%')
    expect(formatPct(0)).toBe('0.0%')
    expect(formatPct(null)).toBe('—')
  })

  it('formats median delays in the unit that reads best', () => {
    expect(formatHours(0.5)).toBe('30 min')
    expect(formatHours(30)).toBe('30.0 h')
    expect(formatHours(72)).toBe('3.0 d')
    expect(formatHours(null)).toBe('—')
  })

  it('labels sources, falling back to the raw value', () => {
    expect(sourceLabel('before_tracking')).toBe('Before tracking')
    expect(sourceLabel('episode')).toBe('Episode page')
    expect(sourceLabel('newsletter')).toBe('newsletter')
  })

  it('treats fewer than five as a small group', () => {
    expect(isSmallGroup(4)).toBe(true)
    expect(isSmallGroup(5)).toBe(false)
  })
})
