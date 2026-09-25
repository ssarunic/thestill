import { describe, expect, it } from 'vitest'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import { NO_SEGMENT, synthesizeIndexMention } from './indexMention'
import { isSpeakingMention } from './mentionPermalink'

function mention(id: number, segmentId: number, startMs: number, role: string | null = null): MentionLite {
  return {
    id,
    entity_id: 'person:alice',
    segment_id: segmentId,
    start_ms: startMs,
    end_ms: startMs + 1000,
    speaker: null,
    role,
    surface_form: 'Alice',
    quote_excerpt: 'Alice',
    confidence: 0.9,
    sentiment: null,
  }
}

const ALICE: EpisodeEntity = {
  entity: { id: 'person:alice', type: 'person', canonical_name: 'Alice', wikidata_qid: null },
  mention_count: 3,
  first_mention_ms: 5_000,
  speaker_kind: 'host',
  salience: 3,
  mentions: [mention(2, 20, 65_000), mention(1, 10, 5_000, 'speaking'), mention(3, 30, 125_000)],
}

describe('synthesizeIndexMention', () => {
  it('borrows the earliest transcript mention, whatever its role, and reads as a name in text', () => {
    const m = synthesizeIndexMention(ALICE)
    expect(m.segment_id).toBe(10)
    expect(m.start_ms).toBe(5_000)
    expect(m.entity_id).toBe('person:alice')
    expect(m.surface_form).toBe('Alice')
    expect(isSpeakingMention(m)).toBe(false)
  })

  it('falls back to first_mention_ms with no segment when the payload carries no mentions', () => {
    const m = synthesizeIndexMention({ ...ALICE, mentions: [] })
    expect(m.segment_id).toBe(NO_SEGMENT)
    expect(m.start_ms).toBe(5_000)
  })
})
