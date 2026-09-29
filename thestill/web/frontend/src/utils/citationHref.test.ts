import { describe, expect, it } from 'vitest'
import { parseCitationId } from './citationHref'

describe('parseCitationId', () => {
  it('reads the cite id from a summary citation href', () => {
    expect(parseCitationId('?t=2970&cite=c3')).toBe('c3')
    expect(parseCitationId('?cite=c0')).toBe('c0')
  })

  it('returns null for anything that is not a citation link', () => {
    expect(parseCitationId('?t=2970')).toBeNull()
    expect(parseCitationId('https://example.com/?cite=c3')).toBeNull()
    expect(parseCitationId('/podcasts/x')).toBeNull()
    expect(parseCitationId(undefined)).toBeNull()
    expect(parseCitationId(null)).toBeNull()
  })
})
