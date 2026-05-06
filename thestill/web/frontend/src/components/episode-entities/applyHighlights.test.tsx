import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { applyEntityHighlights, type SegmentMentionSet } from './applyHighlights'
import type { EpisodeEntity, MentionLite } from '../../api/types'

function entity(id: string, name: string): EpisodeEntity {
  return {
    entity: { id, type: 'person', canonical_name: name, wikidata_qid: null },
    mention_count: 1,
    first_mention_ms: 0,
    speaker_kind: 'unknown',
    mentions: [],
  }
}

function mention(
  id: number,
  entityId: string,
  surfaceForm: string,
  confidence = 0.9,
): MentionLite {
  return {
    id,
    entity_id: entityId,
    segment_id: 1,
    start_ms: 0,
    end_ms: 1000,
    speaker: null,
    role: null,
    surface_form: surfaceForm,
    quote_excerpt: surfaceForm,
    confidence,
    sentiment: null,
  }
}

function setOf(entities: EpisodeEntity[], mentions: MentionLite[]): SegmentMentionSet {
  return {
    entityById: new Map(entities.map((e) => [e.entity.id, e])),
    mentions,
  }
}

function renderApplied(node: ReturnType<typeof applyEntityHighlights>) {
  return render(<MemoryRouter>{node as JSX.Element}</MemoryRouter>)
}

describe('applyEntityHighlights', () => {
  it('returns the existing nodes unchanged when entity highlighting is disabled', () => {
    const set = setOf([entity('person:a', 'Alice')], [mention(1, 'person:a', 'Alice')])
    const out = applyEntityHighlights({
      text: 'Alice spoke',
      segmentMentions: set,
      enabled: false,
      existingNodes: 'Alice spoke',
    })
    const { container } = renderApplied(out)
    expect(container.textContent).toBe('Alice spoke')
    expect(container.querySelector('a')).toBeNull()
  })

  it('returns the existing nodes when no mentions are present', () => {
    const out = applyEntityHighlights({
      text: 'no mentions here',
      segmentMentions: null,
      enabled: true,
      existingNodes: 'no mentions here',
    })
    const { container } = renderApplied(out)
    expect(container.textContent).toBe('no mentions here')
    expect(container.querySelector('a')).toBeNull()
  })

  it('drops mentions below the confidence floor (0.5)', () => {
    const set = setOf([entity('person:a', 'Alice')], [mention(1, 'person:a', 'Alice', 0.3)])
    const out = applyEntityHighlights({
      text: 'Alice spoke',
      segmentMentions: set,
      enabled: true,
      existingNodes: 'Alice spoke',
    })
    const { container } = renderApplied(out)
    expect(container.querySelector('a')).toBeNull()
  })

  it('wraps a matched surface form in an entity anchor', () => {
    const set = setOf([entity('person:a', 'Alice')], [mention(1, 'person:a', 'Alice')])
    const out = applyEntityHighlights({
      text: 'Alice spoke',
      segmentMentions: set,
      enabled: true,
      existingNodes: 'Alice spoke',
    })
    const { container } = renderApplied(out)
    const link = container.querySelector('a')
    expect(link).not.toBeNull()
    expect(link!.getAttribute('href')).toBe('/entities/person/a')
    expect(link!.textContent).toBe('Alice')
  })

  it('prefers the longer surface form when two overlap', () => {
    // "Andrej Karpathy" should win over "Karpathy" — the long-first
    // sort places the longer span first; the shorter span overlaps so
    // it's skipped.
    const set = setOf(
      [entity('person:ak', 'Andrej Karpathy'), entity('person:k', 'Karpathy')],
      [
        mention(1, 'person:ak', 'Andrej Karpathy'),
        mention(2, 'person:k', 'Karpathy'),
      ],
    )
    const out = applyEntityHighlights({
      text: 'Andrej Karpathy is here',
      segmentMentions: set,
      enabled: true,
      existingNodes: 'Andrej Karpathy is here',
    })
    const { container } = renderApplied(out)
    const links = container.querySelectorAll('a')
    expect(links).toHaveLength(1)
    expect(links[0].textContent).toBe('Andrej Karpathy')
  })

  it('does case-insensitive matching', () => {
    const set = setOf([entity('person:a', 'Alice')], [mention(1, 'person:a', 'ALICE')])
    const out = applyEntityHighlights({
      text: 'and then alice spoke',
      segmentMentions: set,
      enabled: true,
      existingNodes: 'and then alice spoke',
    })
    const { container } = renderApplied(out)
    expect(container.querySelector('a')!.textContent).toBe('alice')
  })
})
