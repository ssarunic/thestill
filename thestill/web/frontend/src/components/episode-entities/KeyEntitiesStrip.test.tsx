import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import KeyEntitiesStrip from './KeyEntitiesStrip'
import type { EntityType, EpisodeEntity } from '../../api/types'

function entity(
  id: string,
  name: string,
  type: EntityType,
  count: number,
  firstMentionMs = 1000,
): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: count,
    first_mention_ms: firstMentionMs,
    speaker_kind: 'unknown',
    mentions: [],
  }
}

function renderStrip(
  entities: EpisodeEntity[],
  hidden: Set<EntityType> = new Set(),
  onToggle = vi.fn(),
  onSeek?: (s: number) => void,
) {
  return render(
    <MemoryRouter>
      <KeyEntitiesStrip
        entities={entities}
        hiddenTypes={hidden}
        onToggleType={onToggle}
        onSeek={onSeek}
      />
    </MemoryRouter>,
  )
}

describe('KeyEntitiesStrip', () => {
  it('renders nothing when no entities are present', () => {
    const { container } = renderStrip([])
    expect(container.firstChild).toBeNull()
  })

  it('caps the visible pill list to topN by mention count', () => {
    const items = Array.from({ length: 8 }, (_, i) =>
      entity(`person:p${i}`, `Person ${i}`, 'person', 10 - i),
    )
    renderStrip(items)
    // Default topN=5
    expect(screen.getByText('Person 0')).toBeInTheDocument()
    expect(screen.getByText('Person 4')).toBeInTheDocument()
    expect(screen.queryByText('Person 5')).toBeNull()
    expect(screen.queryByText('Person 7')).toBeNull()
  })

  it('hides entities whose type is in the hidden set', () => {
    const items = [
      entity('person:p1', 'Alice', 'person', 5),
      entity('company:c1', 'Acme', 'company', 4),
    ]
    renderStrip(items, new Set(['company']))
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.queryByText('Acme')).toBeNull()
  })

  it('fires onToggleType when a type filter button is clicked', () => {
    const items = [entity('person:p1', 'Alice', 'person', 5)]
    const onToggle = vi.fn()
    renderStrip(items, new Set(), onToggle)
    fireEvent.click(screen.getByLabelText(/Hide persons/))
    expect(onToggle).toHaveBeenCalledWith('person')
  })

  it('fires onSeek with the first-mention seconds when the play button is clicked', () => {
    const items = [entity('person:p1', 'Alice', 'person', 5, 90_000)]
    const onSeek = vi.fn()
    renderStrip(items, new Set(), vi.fn(), onSeek)
    fireEvent.click(screen.getByLabelText(/Play first mention of Alice at 1:30/))
    expect(onSeek).toHaveBeenCalledWith(90)
  })

  it('navigates to the entity page when the pill name is clicked', () => {
    const items = [entity('person:elon-musk', 'Elon Musk', 'person', 5)]
    renderStrip(items)
    const link = screen.getByRole('link', { name: /Elon Musk/ })
    expect(link.getAttribute('href')).toBe('/entities/person/elon-musk')
  })
})
