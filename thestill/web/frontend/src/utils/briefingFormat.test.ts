import { describe, expect, it } from 'vitest'
import { editionTitle, formatUpcomingSlot } from './briefingFormat'

// Local-time constructors so the assertions hold in any test timezone.
const NOW = new Date(2026, 9, 2, 14, 0) // Fri Oct 2, 14:00

describe('editionTitle', () => {
  it('names same-day editions by part of day', () => {
    expect(editionTitle(new Date(2026, 9, 2, 8, 0).toISOString(), NOW)).toBe("This morning's briefing")
    expect(editionTitle(new Date(2026, 9, 2, 13, 0).toISOString(), NOW)).toBe("This afternoon's briefing")
  })

  it('says yesterday rather than today once the day turns', () => {
    expect(editionTitle(new Date(2026, 9, 1, 8, 0).toISOString(), NOW)).toBe("Yesterday's briefing")
  })

  it('uses the weekday within a week, then the date', () => {
    expect(editionTitle(new Date(2026, 8, 28, 8, 0).toISOString(), NOW)).toBe("Monday's briefing")
    expect(editionTitle(new Date(2026, 8, 12, 8, 0).toISOString(), NOW)).toBe('Briefing · Sep 12')
  })
})

describe('formatUpcomingSlot', () => {
  it('says today / tomorrow / weekday', () => {
    expect(formatUpcomingSlot(new Date(2026, 9, 2, 18, 0).toISOString(), NOW)).toMatch(/^today 6:00/)
    expect(formatUpcomingSlot(new Date(2026, 9, 3, 8, 0).toISOString(), NOW)).toMatch(/^tomorrow 8:00/)
    expect(formatUpcomingSlot(new Date(2026, 9, 5, 8, 0).toISOString(), NOW)).toMatch(/^Mon 8:00/)
  })
})
