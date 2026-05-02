import { describe, it, expect } from 'vitest'
import { parseQuery } from './searchOperators'

describe('parseQuery', () => {
  it('returns plain text for a non-operator query', () => {
    const r = parseQuery('musk capex')
    expect(r.text).toBe('musk capex')
    expect(r.filters).toEqual({})
    expect(r.hints).toEqual([])
  })

  it('extracts a single after: operator', () => {
    const r = parseQuery('musk after:2025-01-01')
    expect(r.text).toBe('musk')
    expect(r.filters.date_from).toBe('2025-01-01')
  })

  it('extracts before: operator', () => {
    const r = parseQuery('before:2026-01-01 capex')
    expect(r.text).toBe('capex')
    expect(r.filters.date_to).toBe('2026-01-01')
  })

  it('rejects malformed dates with a hint and keeps the literal', () => {
    const r = parseQuery('musk after:next-week')
    expect(r.text).toBe('musk after:next-week')
    expect(r.filters.date_from).toBeUndefined()
    expect(r.hints).toEqual([
      { operator: 'after', value: 'next-week', reason: 'expected YYYY-MM-DD' },
    ])
  })

  it('extracts person/company/topic into arrays', () => {
    const r = parseQuery('person:elon-musk company:tesla topic:"data centres"')
    expect(r.text).toBe('')
    expect(r.filters.person).toEqual(['elon-musk'])
    expect(r.filters.company).toEqual(['tesla'])
    expect(r.filters.topic).toEqual(['data centres'])
  })

  it('extracts podcast slug', () => {
    const r = parseQuery('podcast:prof-g-markets capex')
    expect(r.text).toBe('capex')
    expect(r.filters.podcast_slug).toBe('prof-g-markets')
  })

  it('keeps operators inside quoted phrases as literal text', () => {
    const r = parseQuery('"after:dinner thoughts" politics')
    // The "after:" inside quotes is part of the phrase, not parsed.
    expect(r.text).toBe('"after:dinner thoughts" politics')
    expect(r.filters.date_from).toBeUndefined()
  })

  it('case-insensitive on operator names', () => {
    const r = parseQuery('Person:Musk After:2025-06-01')
    expect(r.filters.person).toEqual(['Musk'])
    expect(r.filters.date_from).toBe('2025-06-01')
  })

  it('collapses multi-space residue after stripping', () => {
    const r = parseQuery('musk   after:2025-01-01    capex')
    expect(r.text).toBe('musk capex')
  })

  it('supports multiple person: operators', () => {
    const r = parseQuery('person:musk person:tenev')
    expect(r.filters.person).toEqual(['musk', 'tenev'])
  })
})
