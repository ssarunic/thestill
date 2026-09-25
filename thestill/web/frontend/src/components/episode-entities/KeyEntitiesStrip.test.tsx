import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import KeyEntitiesStrip from './KeyEntitiesStrip'
import type { EntityType, EpisodeEntity, MentionLite } from '../../api/types'

vi.mock('../../hooks/useApi', () => ({
  useEntitySummary: vi.fn(() => ({ data: undefined })),
}))

function entity(
  id: string,
  name: string,
  type: EntityType,
  count: number,
  firstMentionMs = 1000,
  mentions: MentionLite[] = [],
): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: count,
    first_mention_ms: firstMentionMs,
    speaker_kind: 'unknown',
    salience: count,
    mentions,
  }
}

function mention(entityId: string, segmentId: number, startMs: number): MentionLite {
  return {
    id: segmentId,
    entity_id: entityId,
    segment_id: segmentId,
    start_ms: startMs,
    end_ms: startMs + 1000,
    speaker: null,
    role: null,
    surface_form: 'x',
    quote_excerpt: 'x',
    confidence: 0.9,
    sentiment: null,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function renderStrip(
  entities: EpisodeEntity[],
  hidden: Set<EntityType> = new Set(),
  onToggle = vi.fn(),
  extra: { onShowInTranscript?: (segmentId: number) => void } = {},
) {
  return render(
    <MemoryRouter initialEntries={['/podcasts/show/episodes/ep-1']}>
      <Routes>
        <Route
          path="*"
          element={
            <>
              <KeyEntitiesStrip entities={entities} hiddenTypes={hidden} onToggleType={onToggle} {...extra} />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

// jsdom has no matchMedia; `useIsSmUp` then falls back to desktop. A
// phone is simulated by stubbing it to not match.
function stubPhone() {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
  )
}

describe('KeyEntitiesStrip', () => {
  it('renders nothing when no entities are present', () => {
    renderStrip([])
    expect(screen.queryByTestId('key-entities-strip')).toBeNull()
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

  it('renders each pill as a plain link with no play button beside it', () => {
    const items = [entity('person:p1', 'Alice', 'person', 5, 90_000)]
    renderStrip(items)
    expect(screen.queryByLabelText(/Play first mention/)).toBeNull()
    expect(screen.getByRole('link', { name: /Alice/ })).toBeInTheDocument()
  })

  it('keeps the entity href on the pill for modifier / middle clicks', () => {
    const items = [entity('person:elon-musk', 'Elon Musk', 'person', 5)]
    renderStrip(items)
    const link = screen.getByRole('link', { name: /Elon Musk/ })
    expect(link.getAttribute('href')).toBe('/entities/person/elon-musk')
  })

  it('a plain click opens a peek in place; the name inside it is the way to the entity page', () => {
    const onShowInTranscript = vi.fn()
    const items = [
      entity('person:elon-musk', 'Elon Musk', 'person', 5, 90_000, [mention('person:elon-musk', 7, 90_000)]),
    ]
    renderStrip(items, new Set(), vi.fn(), { onShowInTranscript })
    const link = screen.getByRole('link', { name: /Elon Musk, Person, 5 mentions/ })
    fireEvent.click(link)
    expect(screen.getByTestId('location')).toHaveTextContent('/podcasts/show/episodes/ep-1')
    const card = screen.getByTestId('entity-hover-card')
    expect(card).toHaveTextContent('5× this episode')

    fireEvent.click(screen.getByRole('button', { name: /Show in transcript/ }))
    expect(onShowInTranscript).toHaveBeenCalledWith(7)
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()

    fireEvent.click(link)
    fireEvent.click(screen.getByTestId('entity-hover-card').querySelector('a[href="/entities/person/elon-musk"]') as HTMLElement)
    expect(screen.getByTestId('location')).toHaveTextContent('/entities/person/elon-musk')
  })

  describe('on a phone', () => {
    afterEach(() => {
      vi.unstubAllGlobals()
    })

    it('a tap opens the peek as a bottom sheet, not a navigation', () => {
      stubPhone()
      renderStrip([entity('company:acme', 'Acme', 'company', 3)])
      const link = screen.getByRole('link', { name: /Acme, Company/ })
      fireEvent.mouseEnter(link)
      expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
      fireEvent.click(link)
      expect(screen.getByTestId('entity-peek-sheet')).toHaveAttribute('aria-label', 'Acme — Company')
      expect(screen.getByTestId('location')).toHaveTextContent('/podcasts/show/episodes/ep-1')
    })
  })
})
