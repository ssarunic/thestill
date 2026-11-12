import { describe, expect, it } from 'vitest'
import { deepLinkParam, deepLinkSeconds } from './useDeepLinkSeek'

describe('deepLinkParam', () => {
  it('reads the query string first, then the fragment', () => {
    expect(deepLinkParam('?t=12', '', 't')).toBe('12')
    expect(deepLinkParam('', '#t=238', 't')).toBe('238')
    expect(deepLinkParam('?t=12', '#t=238', 't')).toBe('12')
    expect(deepLinkParam('?view=transcript', '#about=twilio', 'about')).toBe('twilio')
  })

  it('returns null when neither has it, including plain anchors', () => {
    expect(deepLinkParam('', '', 't')).toBeNull()
    expect(deepLinkParam('?speaker=x', '#quote-12', 't')).toBeNull()
  })
})

describe('deepLinkSeconds', () => {
  it('reads a time from the query or the fragment', () => {
    expect(deepLinkSeconds('?t=0', '')).toBe(0)
    expect(deepLinkSeconds('?view=transcript&t=12.5', '')).toBe(12.5)
    expect(deepLinkSeconds('', '#t=1749')).toBe(1749)
  })

  it('is null for a missing, empty, negative or non-numeric t', () => {
    expect(deepLinkSeconds('', '')).toBeNull()
    expect(deepLinkSeconds('?t=', '')).toBeNull()
    expect(deepLinkSeconds('?t=-5', '')).toBeNull()
    expect(deepLinkSeconds('?t=soon', '')).toBeNull()
    expect(deepLinkSeconds('', '#quote-12')).toBeNull()
  })
})
