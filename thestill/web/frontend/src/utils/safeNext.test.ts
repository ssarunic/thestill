import { describe, expect, it } from 'vitest'
import { safeNextPath } from './safeNext'

describe('safeNextPath (spec #94)', () => {
  it('keeps same-origin paths', () => {
    expect(safeNextPath('/podcasts/a/episodes/b?q=12')).toBe('/podcasts/a/episodes/b?q=12')
    expect(safeNextPath('/entities/person/x?about=y')).toBe('/entities/person/x?about=y')
  })

  it.each([null, '', 'https://evil.example', '//evil.example', '/\\evil', 'x', '/login', '/login?next=/a', '/api/x', '/a\nb'])(
    'drops %s',
    (value) => {
      expect(safeNextPath(value)).toBeNull()
    },
  )
})
