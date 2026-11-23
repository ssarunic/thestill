import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import EntityRail from './EntityRail'
import type { EntityType, EpisodeEntity, MentionLite, RelatedEpisode, SpeakerKind } from '../../api/types'

vi.mock('../../hooks/useApi', () => ({
  useEntitySummary: vi.fn(() => ({ data: undefined })),
}))

function entity(
  id: string,
  name: string,
  type: EntityType = 'person',
  count = 1,
  speakerKind: SpeakerKind = 'unknown',
  firstMentionMs = 0,
  mentions: MentionLite[] = [],
): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: count,
    first_mention_ms: firstMentionMs,
    speaker_kind: speakerKind,
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

function relatedEpisode(overrides: Partial<RelatedEpisode> = {}): RelatedEpisode {
  return {
    episode_id: 'e2',
    podcast_id: 'p1',
    podcast_slug: 'prof-g-markets',
    episode_slug: 'ai-capex-cliff',
    podcast_title: 'Prof G Markets',
    episode_title: 'AI Capex Cliff',
    published_at: '2026-03-14T00:00:00',
    image_url: null,
    score: 0.2,
    ...overrides,
  }
}

function renderRail(
  entities: EpisodeEntity[],
  onSeek?: (s: number) => void,
  opts: {
    relatedEpisodes?: RelatedEpisode[]
    relatedLoading?: boolean
    extractionPending?: boolean
    onShowInTranscript?: (segmentId: number) => void
  } = {},
) {
  return render(
    <MemoryRouter>
      <EntityRail
        entities={entities}
        onSeek={onSeek}
        onShowInTranscript={opts.onShowInTranscript}
        relatedEpisodes={opts.relatedEpisodes}
        relatedLoading={opts.relatedLoading}
        extractionPending={opts.extractionPending}
      />
    </MemoryRouter>,
  )
}

describe('EntityRail', () => {
  it('renders an empty-state message when no entities are present', () => {
    renderRail([])
    expect(screen.getByText(/No entities extracted/)).toBeInTheDocument()
    expect(screen.queryByText('In this episode')).toBeNull()
    expect(screen.queryByText('People mentioned')).toBeNull()
    // Nothing to list at all: one combined note, no bare section header.
    expect(screen.queryByText('Related episodes')).toBeNull()
  })

  it('explains that the rail fills in later while extraction is still pending', () => {
    renderRail([], undefined, { extractionPending: true })
    expect(screen.getByText(/once the transcript has been processed/)).toBeInTheDocument()
    expect(screen.queryByText(/No entities extracted/)).toBeNull()
  })

  it('groups entities into People / Companies / Topics sections', () => {
    renderRail([
      entity('person:a', 'Alice', 'person', 5),
      entity('company:b', 'Acme', 'company', 3),
      entity('topic:c', 'Compute', 'topic', 2),
    ])
    expect(screen.getByText('People mentioned')).toBeInTheDocument()
    // Nobody takes part, so there is no cast section.
    expect(screen.queryByText('In this episode')).toBeNull()
    expect(screen.getByText('Companies mentioned')).toBeInTheDocument()
    expect(screen.getByText('Topics')).toBeInTheDocument()
  })

  it('lists the people who take part apart from the people only mentioned, guests first', () => {
    renderRail([
      // Host-first, as a payload cached from before the order changed.
      entity('person:host', 'Hosty', 'person', 81, 'host'),
      entity('person:guest', 'Guesty', 'person', 253, 'guest'),
      entity('person:regular', 'Regular', 'person', 4, 'recurring'),
      entity('person:other', 'Stranger', 'person', 7, 'unknown'),
    ])
    const cast = screen.getByText('In this episode').closest('section') as HTMLElement
    const mentioned = screen.getByText('People mentioned').closest('section') as HTMLElement
    expect(within(cast).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Guestyguest',
      'Hostyhost',
      'Regularrecurring',
    ])
    // A participant's count is their own speaking turns: not shown.
    expect(within(cast).queryByText(/×/)).toBeNull()
    expect(within(mentioned).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Stranger7×'])
  })

  it('keeps an absent host who is only talked about among the people mentioned', () => {
    renderRail([
      { ...entity('person:guest', 'Guesty', 'person', 40, 'guest'), takes_part: true },
      // The show's host, not on this episode: discussed three times.
      { ...entity('person:host', 'Hosty', 'person', 3, 'host'), takes_part: false },
    ])
    const cast = screen.getByText('In this episode').closest('section') as HTMLElement
    const mentioned = screen.getByText('People mentioned').closest('section') as HTMLElement
    expect(within(cast).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Guestyguest'])
    // Still tagged as the host, and the count is back: these are mentions.
    expect(within(mentioned).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Hostyhost3×'])
  })

  it('never folds the people who take part behind "Show all"', () => {
    renderRail(Array.from({ length: 10 }, (_, i) => entity(`person:g${i}`, `Guest ${i}`, 'person', 1, 'guest')))
    expect(screen.getAllByRole('listitem')).toHaveLength(10)
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })

  it('keeps the count on a company that is also a host anchor', () => {
    renderRail([entity('company:acme', 'Acme', 'company', 3, 'host')])
    const section = screen.getByText('Companies mentioned').closest('section') as HTMLElement
    expect(within(section).getByText('3×')).toBeInTheDocument()
    expect(screen.queryByText('In this episode')).toBeNull()
  })

  it('shows a host/guest tag for participants', () => {
    renderRail([
      entity('person:host', 'Hosty', 'person', 5, 'host'),
      entity('person:other', 'Stranger', 'person', 1, 'unknown'),
    ])
    expect(screen.getByText('host')).toBeInTheDocument()
    // The 'unknown' speaker_kind is suppressed (rendered without a tag).
    expect(screen.queryByText('unknown')).toBeNull()
  })

  it('renders the entity name as a link to the entity page', () => {
    renderRail([entity('person:elon-musk', 'Elon Musk', 'person', 5)])
    const link = screen.getByRole('link', { name: /Elon Musk/ })
    expect(link.getAttribute('href')).toBe('/entities/person/elon-musk')
  })

  it('a click on the name opens a peek in place instead of navigating', () => {
    const onShowInTranscript = vi.fn()
    renderRail(
      [
        entity('person:alice', 'Alice', 'person', 2, 'guest', 65_000, [
          mention('person:alice', 20, 65_000),
          mention('person:alice', 30, 125_000),
        ]),
      ],
      undefined,
      { onShowInTranscript },
    )
    const link = screen.getByRole('link', { name: /Alice, Person, 2 mentions/ })
    // fireEvent returns false when the handler called preventDefault — no navigation.
    expect(fireEvent.click(link)).toBe(false)
    const card = screen.getByTestId('entity-hover-card')
    expect(card).toHaveTextContent('2× this episode')
    expect(card.querySelector('a[href="/entities/person/alice"]')).toHaveTextContent('Alice')

    fireEvent.click(screen.getByRole('button', { name: /Show in transcript/ }))
    expect(onShowInTranscript).toHaveBeenCalledWith(20)
    expect(screen.queryByTestId('entity-hover-card')).not.toBeInTheDocument()
  })

  it('does not offer Show in transcript when the payload carries no mentions', () => {
    renderRail([entity('person:alice', 'Alice', 'person', 2)], undefined, { onShowInTranscript: vi.fn() })
    fireEvent.click(screen.getByRole('link', { name: /Alice, Person/ }))
    expect(screen.getByTestId('entity-hover-card')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Show in transcript/ })).not.toBeInTheDocument()
  })

  it('fires onSeek with the first-mention seconds when the play button is clicked', () => {
    const onSeek = vi.fn()
    renderRail([entity('person:a', 'Alice', 'person', 1, 'unknown', 12_500)], onSeek)
    fireEvent.click(screen.getByLabelText(/Play first mention of Alice/))
    expect(onSeek).toHaveBeenCalledWith(12.5)
  })

  it('keeps the Related episodes slot with a note when none are found', () => {
    renderRail([entity('person:a', 'Alice', 'person', 1)])
    expect(screen.getByText('Related episodes')).toBeInTheDocument()
    expect(screen.getByText('No related episodes yet.')).toBeInTheDocument()
  })

  it('uses pending copy in the Related episodes slot while extraction is in flight', () => {
    renderRail([entity('person:a', 'Alice', 'person', 1)], undefined, { extractionPending: true })
    expect(screen.getByText(/Related episodes appear here once/)).toBeInTheDocument()
  })

  it('shows a loading note while related episodes are being fetched', () => {
    renderRail([entity('person:a', 'Alice', 'person', 1)], undefined, { relatedLoading: true })
    expect(screen.getByText('Related episodes')).toBeInTheDocument()
    expect(screen.getByText(/Finding related episodes/)).toBeInTheDocument()
  })

  it('renders related episode cards linking to the episode page', () => {
    renderRail([entity('person:a', 'Alice', 'person', 1)], undefined, {
      relatedEpisodes: [relatedEpisode()],
    })
    expect(screen.getByText('Related episodes')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /AI Capex Cliff/ })
    expect(link.getAttribute('href')).toBe('/podcasts/prof-g-markets/episodes/ai-capex-cliff')
  })

  it('surfaces related episodes even when no entities were extracted', () => {
    renderRail([], undefined, { relatedEpisodes: [relatedEpisode()] })
    // No entity buckets, but the related section still renders and the
    // "no entities" note is suppressed.
    expect(screen.getByText('Related episodes')).toBeInTheDocument()
    expect(screen.queryByText(/No entities extracted/)).toBeNull()
  })

  it('caps a section at 8 visible entries by default and reveals the rest on expand', () => {
    // Spec #28 §5.2 — sections beyond 8 collapse the tail behind a
    // "Show all (N)" toggle so the default rail height stays scannable.
    const persons = Array.from({ length: 12 }, (_, i) =>
      entity(`person:${i}`, `Person ${i + 1}`, 'person', 12 - i),
    )
    renderRail(persons)
    expect(screen.getByText('Person 1')).toBeInTheDocument()
    expect(screen.getByText('Person 8')).toBeInTheDocument()
    // 9..12 are collapsed
    expect(screen.queryByText('Person 9')).toBeNull()
    expect(screen.queryByText('Person 12')).toBeNull()

    const toggle = screen.getByRole('button', { name: /Show all \(12\)/ })
    fireEvent.click(toggle)
    expect(screen.getByText('Person 9')).toBeInTheDocument()
    expect(screen.getByText('Person 12')).toBeInTheDocument()
    // The button flips to "Show fewer" when expanded.
    expect(screen.getByRole('button', { name: /Show fewer/ })).toBeInTheDocument()
  })

  it('does not render the expander when a section already fits under the cap', () => {
    renderRail([
      entity('person:a', 'Alice', 'person', 5),
      entity('person:b', 'Bob', 'person', 3),
    ])
    expect(screen.queryByRole('button', { name: /Show all/ })).toBeNull()
  })
})

describe('EntityRail type toggles (the strip is not shown from lg up)', () => {
  const renderRail = (props: Partial<Parameters<typeof EntityRail>[0]> = {}) =>
    render(
      <MemoryRouter>
        <EntityRail entities={[entity('person:a', 'Ada'), entity('company:b', 'Acme', 'company')]} {...props} />
      </MemoryRouter>,
    )

  it('carries the type toggles and reports a click', () => {
    const onToggleType = vi.fn()
    renderRail({ hiddenTypes: new Set<EntityType>(['company']), onToggleType })
    const toggles = within(screen.getByTestId('entity-rail-type-toggles'))
    expect(toggles.getByLabelText('Hide persons')).toHaveAttribute('aria-pressed', 'true')
    expect(toggles.getByLabelText('Show companys')).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(toggles.getByLabelText('Hide persons'))
    expect(onToggleType).toHaveBeenCalledWith('person')
    // The rail is the index: a type hidden from the highlights is still listed.
    expect(screen.getByText('Acme')).toBeInTheDocument()
  })

  it('shows no toggles without the props, or when the episode has no entities', () => {
    renderRail()
    expect(screen.queryByTestId('entity-rail-type-toggles')).toBeNull()
    render(
      <MemoryRouter>
        <EntityRail entities={[]} hiddenTypes={new Set()} onToggleType={vi.fn()} />
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('entity-rail-type-toggles')).toBeNull()
  })
})
