import { describe, expect, it } from 'vitest'
import { formatWhen } from './adminFormat'

describe('formatWhen', () => {
  it('shows the time as well as the date', () => {
    const shown = formatWhen('2026-10-19T00:42:18+00:00')
    expect(shown).toMatch(/\d{1,2}[:.]\d{2}/)
    expect(shown).not.toBe(new Date('2026-10-19T00:42:18+00:00').toLocaleDateString())
  })

  it('shows a dash when there is no timestamp', () => {
    expect(formatWhen(null)).toBe('—')
  })
})
