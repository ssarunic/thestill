import { describe, expect, it } from 'vitest'
import { deepLinkParam } from './useDeepLinkSeek'

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
