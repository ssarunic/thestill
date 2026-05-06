import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import EntityFilterBar from './EntityFilterBar'
import type { EntityType, EpisodeEntity } from '../../api/types'

function entity(
  id: string,
  name: string,
  type: EntityType = 'person',
  count = 1,
): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: count,
    first_mention_ms: 0,
    speaker_kind: 'unknown',
    mentions: [],
  }
}

describe('EntityFilterBar', () => {
  it('renders nothing when there are no entities', () => {
    const { container } = render(
      <EntityFilterBar entities={[]} selectedEntityIds={new Set()} onToggle={vi.fn()} onClear={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('shows the filter prompt when nothing is selected', () => {
    render(
      <EntityFilterBar
        entities={[entity('person:a', 'Alice')]}
        selectedEntityIds={new Set()}
        onToggle={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    expect(screen.getByText('Filter by entity')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull()
  })

  it('shows the "Showing only" label and Clear button when something is selected', () => {
    const onClear = vi.fn()
    render(
      <EntityFilterBar
        entities={[entity('person:a', 'Alice'), entity('person:b', 'Bob')]}
        selectedEntityIds={new Set(['person:a'])}
        onToggle={vi.fn()}
        onClear={onClear}
      />,
    )
    expect(screen.getByText('Showing only:')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onClear).toHaveBeenCalledOnce()
  })

  it('fires onToggle with the entity id when a chip is clicked', () => {
    const onToggle = vi.fn()
    render(
      <EntityFilterBar
        entities={[entity('person:a', 'Alice')]}
        selectedEntityIds={new Set()}
        onToggle={onToggle}
        onClear={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /Alice/ }))
    expect(onToggle).toHaveBeenCalledWith('person:a')
  })

  it('keeps a selected chip visible even when it falls outside the top-12', () => {
    const top = Array.from({ length: 12 }, (_, i) =>
      entity(`person:top${i}`, `Top ${i}`, 'person', 100 - i),
    )
    const lonely = entity('person:lonely', 'Lonely', 'person', 1)
    render(
      <EntityFilterBar
        entities={[...top, lonely]}
        selectedEntityIds={new Set(['person:lonely'])}
        onToggle={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /Lonely/ })).toBeInTheDocument()
  })

  it('marks a selected chip via aria-pressed', () => {
    render(
      <EntityFilterBar
        entities={[entity('person:a', 'Alice')]}
        selectedEntityIds={new Set(['person:a'])}
        onToggle={vi.fn()}
        onClear={vi.fn()}
      />,
    )
    const chip = screen.getByRole('button', { name: /Alice/ })
    expect(chip).toHaveAttribute('aria-pressed', 'true')
  })
})
