import { describe, expect, it } from 'vitest'
import type { EpisodeEntity, MentionLite } from '../../api/types'
import { buildEntityTerms, buildEntityTermIndex, matchEntityTerms } from './entityTerms'

function mention(entityId: string, surface: string, overrides: Partial<MentionLite> = {}): MentionLite {
  return {
    id: 0,
    entity_id: entityId,
    segment_id: 1,
    start_ms: 0,
    end_ms: 1000,
    speaker: null,
    role: null,
    surface_form: surface,
    quote_excerpt: surface,
    confidence: 0.9,
    sentiment: null,
    ...overrides,
  }
}

function entity(id: string, name: string, mentions: MentionLite[] = [], type: EpisodeEntity['entity']['type'] = 'person'): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: mentions.length,
    first_mention_ms: 0,
    speaker_kind: 'unknown',
    salience: 1,
    mentions,
  }
}

const KARPATHY = entity('person:karpathy', 'Andrej Karpathy', [
  mention('person:karpathy', 'Karpathy'),
  mention('person:karpathy', 'Andrej'),
])
const WARP = entity('company:warp', 'Warp', [mention('company:warp', 'Warp')], 'company')

function terms(entities: EpisodeEntity[]): string[] {
  return buildEntityTerms(entities).map((t) => t.term)
}

function matches(text: string, entities: EpisodeEntity[]) {
  return matchEntityTerms(text, buildEntityTermIndex(entities)).map((m) => [m.text, m.entityId])
}

describe('buildEntityTerms', () => {
  it('lists the canonical name and surface forms, longest first', () => {
    expect(terms([KARPATHY])).toEqual(['Andrej Karpathy', 'Karpathy', 'Andrej'])
  })

  it('is case-sensitive for single tokens and case-insensitive for phrases', () => {
    const built = buildEntityTerms([KARPATHY])
    expect(built.find((t) => t.term === 'Andrej Karpathy')?.caseSensitive).toBe(false)
    expect(built.find((t) => t.term === 'Karpathy')?.caseSensitive).toBe(true)
  })

  it('drops short terms and lowercase single tokens', () => {
    const e = entity('company:warp', 'Warp', [
      mention('company:warp', 'warp'),
      mention('company:warp', 'PR'),
      mention('company:warp', 'MCP'),
    ], 'company')
    expect(terms([e])).toEqual(['Warp', 'MCP'])
  })

  it('drops a term two entities share', () => {
    const alexA = entity('person:alex-a', 'Alex Alpha', [mention('person:alex-a', 'Alex')])
    const alexB = entity('person:alex-b', 'Alex Beta', [mention('person:alex-b', 'Alex')])
    expect(terms([alexA, alexB])).toEqual(['Alex Alpha', 'Alex Beta'])
  })

  it('ignores speaking mentions and low-confidence surface forms', () => {
    const e = entity('person:claire', 'Claire Vo', [
      mention('person:claire', 'SPEAKER_01', { role: 'speaking' }),
      mention('person:claire', 'Claire', { confidence: 0.2 }),
      mention('person:claire', 'Vo'),
    ])
    expect(terms([e])).toEqual(['Claire Vo'])
  })

  it('collapses whitespace in a candidate', () => {
    expect(terms([entity('person:x', '  Zach   Lloyd ')])).toEqual(['Zach Lloyd'])
  })
})

describe('matchEntityTerms', () => {
  it('finds every occurrence with word boundaries', () => {
    expect(matches('Warp ships. Warped is not Warp, nor is warp.', [WARP])).toEqual([
      ['Warp', 'company:warp'],
      ['Warp', 'company:warp'],
    ])
  })

  it('matches phrases regardless of case and across a line break', () => {
    expect(matches('andrej karpathy and Andrej\nKarpathy', [KARPATHY])).toEqual([
      ['andrej karpathy', 'person:karpathy'],
      ['Andrej\nKarpathy', 'person:karpathy'],
    ])
  })

  it('prefers the longest term at a position and never overlaps', () => {
    const out = matchEntityTerms('Andrej Karpathy, then Karpathy again', buildEntityTermIndex([KARPATHY]))
    expect(out.map((m) => [m.start, m.end, m.text])).toEqual([
      [0, 15, 'Andrej Karpathy'],
      [22, 30, 'Karpathy'],
    ])
  })

  it('does not match inside a longer word in either direction', () => {
    const vo = entity('person:vo', 'Vo Inc', [mention('person:vo', 'Vox')])
    expect(matches('Volvo Voxel Vox', [vo])).toEqual([['Vox', 'person:vo']])
  })

  it('returns nothing for an empty index', () => {
    expect(matches('Warp', [])).toEqual([])
  })
})
