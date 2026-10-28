import { describe, expect, it } from 'vitest'
import { describeAuditEntry, formatWhen } from './adminFormat'

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

describe('describeAuditEntry — entity curation', () => {
  const entry = (action: string, details: Record<string, unknown>) =>
    ({ action, details }) as unknown as Parameters<typeof describeAuditEntry>[0]

  it('names what moved where', () => {
    expect(describeAuditEntry(entry('entity.merge', { keeper: 'company:doximity', loser: 'topic:doximity' }))).toBe(
      'Merged topic:doximity into company:doximity',
    )
    expect(
      describeAuditEntry(
        entry('entity.split', { entity_id: 'person:islam-makhachev', from_entity_id: 'person:islam', moved: 15 }),
      ),
    ).toBe('Split person:islam-makhachev out of person:islam (15 mentions)')
    expect(
      describeAuditEntry(entry('entity.relink', { entity_id: 'company:perplexity', old: 'Q372193', new: 'Q123403392', reason: 'wrong item' })),
    ).toBe('company:perplexity: Q372193 → Q123403392 — “wrong item”')
    expect(
      describeAuditEntry(entry('entity.set_type', { entity_id: 'product:android-17', old: 'person', new: 'product', reason: 'the OS' })),
    ).toBe('product:android-17: person → product — “the OS”')
  })
})
