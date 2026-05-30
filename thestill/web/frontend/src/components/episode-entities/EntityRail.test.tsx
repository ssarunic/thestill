import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import EntityRail from './EntityRail'
import type { EntityType, EpisodeEntity, RelatedEpisode, SpeakerKind } from '../../api/types'

function entity(
  id: string,
  name: string,
  type: EntityType = 'person',
  count = 1,
  speakerKind: SpeakerKind = 'unknown',
  firstMentionMs = 0,
): EpisodeEntity {
  return {
    entity: { id, type, canonical_name: name, wikidata_qid: null },
    mention_count: count,
    first_mention_ms: firstMentionMs,
    speaker_kind: speakerKind,
    salience: count,
    mentions: [],
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
  opts: { relatedEpisodes?: RelatedEpisode[]; relatedLoading?: boolean } = {},
) {
  return render(
    <MemoryRouter>
      <EntityRail
        entities={entities}
        onSeek={onSeek}
        relatedEpisodes={opts.relatedEpisodes}
        relatedLoading={opts.relatedLoading}
      />
    </MemoryRouter>,
  )
}

describe('EntityRail', () => {
  it('renders an empty-state message when no entities are present', () => {
    renderRail([])
    expect(screen.getByText(/No entities extracted/)).toBeInTheDocument()
    expect(screen.queryByText('People in this episode')).toBeNull()
  })

  it('groups entities into People / Companies / Topics sections', () => {
    renderRail([
      entity('person:a', 'Alice', 'person', 5),
      entity('company:b', 'Acme', 'company', 3),
      entity('topic:c', 'Compute', 'topic', 2),
    ])
    expect(screen.getByText('People in this episode')).toBeInTheDocument()
    expect(screen.getByText('Companies mentioned')).toBeInTheDocument()
    expect(screen.getByText('Topics')).toBeInTheDocument()
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

  it('fires onSeek with the first-mention seconds when the play button is clicked', () => {
    const onSeek = vi.fn()
    renderRail([entity('person:a', 'Alice', 'person', 1, 'unknown', 12_500)], onSeek)
    fireEvent.click(screen.getByLabelText(/Play first mention of Alice/))
    expect(onSeek).toHaveBeenCalledWith(12.5)
  })

  it('omits the Related episodes section when none are found and not loading', () => {
    renderRail([entity('person:a', 'Alice', 'person', 1)])
    expect(screen.queryByText('Related episodes')).toBeNull()
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
