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

describe('describeAuditEntry — CLI and correction curation', () => {
  const entry = (action: string, details: Record<string, unknown>) =>
    ({ action, details }) as unknown as Parameters<typeof describeAuditEntry>[0]

  it('describes mention, role and show edits', () => {
    expect(
      describeAuditEntry(entry('mention.drop', { mention_id: 7, surface_form: 'Ada', scope: 'global', reason: 'ad read' })),
    ).toBe('Dropped mention 7 “Ada” (global) — “ad read”')
    expect(
      describeAuditEntry(entry('podcast.set_hosts', { podcast_id: 'p1', old: [], new: ['person:ada'] })),
    ).toBe('p1: hosts [] → ["person:ada"]')
    expect(describeAuditEntry(entry('episode.set_guests', { episode_id: 'e1', old: ['a'], new: [] }))).toBe(
      'e1: guests ["a"] → []',
    )
    expect(
      describeAuditEntry(entry('podcast.set_summary_profile', { podcast_id: 'p1', old: null, new: 'investing' })),
    ).toBe('p1: summary profile auto → investing')
    expect(
      describeAuditEntry(entry('entity.correction', { correction: 'blacklist', surface_form: 'Anthropic', affected_mentions: 3 })),
    ).toBe('Correction blacklist on “Anthropic” (3 mentions)')
  })
})
