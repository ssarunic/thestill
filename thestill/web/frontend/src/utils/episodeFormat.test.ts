import { describe, it, expect } from 'vitest'
import {
  episodeNumberLabel,
  episodeTypeLabel,
  formatEyebrowDate,
  formatLength,
  formatMinutes,
  formatPublished,
  hostOf,
  languageName,
} from './episodeFormat'

describe('episodeFormat (spec #76)', () => {
  it('formats the eyebrow date with the year only when it differs', () => {
    const now = new Date('2026-09-07T12:00:00Z')
    expect(formatEyebrowDate('2026-09-06T07:00:00Z', now)).toMatch(/^Sun,? 6 Sept?$/)
    expect(formatEyebrowDate('2025-09-06T07:00:00Z', now)).toMatch(/2025/)
    expect(formatEyebrowDate(null, now)).toBeNull()
    expect(formatEyebrowDate('not a date', now)).toBeNull()
  })

  it('formats published date and time', () => {
    expect(formatPublished('2026-09-06T07:00:00Z')).toMatch(/6 Sept? 2026/)
    expect(formatPublished(null)).toBeNull()
  })

  it('formats lengths and minutes', () => {
    expect(formatLength(3505)).toBe('58 min 25 s')
    expect(formatLength(3600)).toBe('1 h')
    expect(formatLength(4081)).toBe('1 h 8 min')
    expect(formatLength(120)).toBe('2 min')
    expect(formatLength(0)).toBeNull()
    expect(formatMinutes(3505)).toBe('58 min')
    expect(formatMinutes(20)).toBe('1 min')
    expect(formatMinutes(null)).toBeNull()
  })

  it('names languages and falls back to the code', () => {
    expect(languageName('en')).toBe('English')
    expect(languageName('hr-HR')).toBe('Croatian')
    expect(languageName('zz')).toBe('ZZ')
    expect(languageName('')).toBeNull()
  })

  it('extracts a host and builds episode labels', () => {
    expect(hostOf('https://www.profgmedia.com/ep/1')).toBe('profgmedia.com')
    expect(hostOf('nope')).toBeNull()
    expect(episodeNumberLabel(3, 12)).toBe('S3 E12')
    expect(episodeNumberLabel(null, 12)).toBe('E12')
    expect(episodeNumberLabel(3, null)).toBe('S3')
    expect(episodeNumberLabel(null, null)).toBeNull()
    expect(episodeTypeLabel('bonus')).toBe('Bonus')
    expect(episodeTypeLabel('full')).toBeNull()
    expect(episodeTypeLabel(null)).toBeNull()
  })
})
