import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import People from './People'
import { buildPeople, normalizePersonName } from './buildPeople'
import type { AnnotatedSegment, EpisodeEntity } from '../../api/types'

function person(id: string, name: string, speaker_kind: EpisodeEntity['speaker_kind'], salience: number, type: EpisodeEntity['entity']['type'] = 'person'): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: 1,
    first_mention_ms: 0,
    speaker_kind,
    salience,
    mentions: [],
  }
}

function segment(id: number, speaker: string | null, kind: AnnotatedSegment['kind'] = 'content'): AnnotatedSegment {
  return { id, start: id, end: id + 1, speaker, text: '', kind, sponsor: null, source_segment_ids: [], source_word_span: null, user_segment_id: null, metadata: {} }
}

describe('buildPeople (spec #76 §3.5)', () => {
  it('lists host/guest person entities by salience, then uncovered speakers, skipping placeholders', () => {
    const entities = [
      person('e1', 'Ed Elson', 'host', 0.5),
      person('e2', 'Jim VandeHei', 'guest', 0.9),
      person('e3', 'Some Company', 'unknown', 1, 'company'),
      person('e4', 'Mentioned Person', 'unknown', 1),
      person('e5', 'Recurring Guest', 'recurring', 1),
    ]
    const segments = [
      segment(0, 'Announcer', 'ad_break'),
      segment(1, 'ed elson'),
      segment(2, 'SPEAKER_00'),
      segment(3, 'Unknown'),
      segment(4, 'Scott'),
      segment(5, ' Scott '),
      segment(6, null),
      segment(7, 'DJ', 'music'),
    ]
    const chips = buildPeople(entities, segments)
    // Ad-break/music speakers never become people, and never take a palette slot.
    expect(chips.map((c) => c.name)).toEqual(['Jim VandeHei', 'Ed Elson', 'Scott'])
    expect(chips[0].href).toContain('/entities/')
    expect(chips[0].segmentId).toBeNull()
    expect(chips[2].href).toBeNull()
    // The jump target is the first segment carrying the label, even when a
    // later segment spells it with stray whitespace.
    expect(chips[2].segmentId).toBe(4)
  })

  it('caps entity people at eight and keeps Jim and James apart', () => {
    const entities = Array.from({ length: 10 }, (_, i) => person(`e${i}`, `Person ${i}`, 'guest', 10 - i))
    const chips = buildPeople(entities, [segment(1, 'Jim'), segment(2, 'James Person')])
    expect(chips).toHaveLength(10)
    expect(chips.slice(8).map((c) => c.name)).toEqual(['Jim', 'James Person'])
  })

  it('legacy transcripts contribute nothing; empty both is empty', () => {
    expect(buildPeople([person('e1', 'Ed', 'host', 1)], null)).toHaveLength(1)
    expect(buildPeople([], null)).toEqual([])
    expect(buildPeople([], undefined)).toEqual([])
  })

  it('normalises names by trim, whitespace and case only', () => {
    expect(normalizePersonName('  Jim   VandeHei ')).toBe('jim vandehei')
  })
})

describe('People', () => {
  it('renders nothing when empty and jumps to speakers when tapped', async () => {
    const onSpeakerSelect = vi.fn()
    const { container } = render(
      <MemoryRouter>
        <People entities={[]} segments={null} onSpeakerSelect={onSpeakerSelect} />
      </MemoryRouter>,
    )
    expect(container).toBeEmptyDOMElement()

    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <People entities={[person('e1', 'Ed Elson', 'host', 1)]} segments={[segment(1, 'Scott')]} onSpeakerSelect={onSpeakerSelect} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('link', { name: 'Ed Elson' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Scott' }))
    expect(onSpeakerSelect).toHaveBeenCalledWith(1)
  })
})
