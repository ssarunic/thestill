import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import type { EpisodeEntity, MentionLite, SummaryCitation } from '../api/types'
import SummaryViewer from './SummaryViewer'

vi.mock('../hooks/useApi', () => ({
  useEntitySummary: vi.fn(() => ({ data: undefined })),
}))

function citation(overrides: Partial<SummaryCitation> = {}): SummaryCitation {
  return {
    id: 'c3',
    raw_label: '49:30',
    cited_playback_s: 2970,
    target_playback_s: 2970,
    segment_id_hint: 42,
    source_segment_ids: [1001],
    resolved: true,
    ...overrides,
  }
}

describe('SummaryViewer citation links', () => {
  it('renders resolved citation links as clickable timestamp chips', () => {
    const onCite = vi.fn()
    const item = citation()

    render(
      <SummaryViewer
        content="Source: [49:30](?t=2970&cite=c3)"
        available
        citations={[item]}
        onCite={onCite}
      />,
    )

    const chip = screen.getByRole('button', {
      name: 'Play summary citation at 49:30',
    })
    expect(chip).toHaveTextContent('49:30')

    fireEvent.click(chip)

    expect(onCite).toHaveBeenCalledTimes(1)
    expect(onCite).toHaveBeenCalledWith(item)
  })

  it('renders unknown citation ids as plain text', () => {
    const { container } = render(
      <SummaryViewer
        content="Source: [10:00](?t=600&cite=missing)"
        available
        citations={[]}
        onCite={vi.fn()}
      />,
    )

    expect(screen.queryByRole('button', { name: /10:00/ })).toBeNull()
    expect(screen.queryByRole('link', { name: '10:00' })).toBeNull()
    expect(container.querySelector('p')?.textContent).toBe('Source: 10:00')
  })

  it('keeps ordinary external markdown links as anchors', () => {
    render(
      <SummaryViewer
        content="[Project site](https://example.com)"
        available
      />,
    )

    const link = screen.getByRole('link', { name: 'Project site' })
    expect(link).toHaveAttribute('href', 'https://example.com')
    expect(link).toHaveAttribute('target', '_blank')
  })
})

// Spec #82 — names in the summary that match this episode's entities are
// entity peeks; the mention behind each borrows the block's nearest
// citation so "Show in transcript" lands on the cited passage.
describe('SummaryViewer entity links', () => {
  function mention(entityId: string, segmentId: number, startMs: number, surface: string): MentionLite {
    return {
      id: 0,
      entity_id: entityId,
      segment_id: segmentId,
      start_ms: startMs,
      end_ms: startMs + 1000,
      speaker: null,
      role: null,
      surface_form: surface,
      quote_excerpt: surface,
      confidence: 0.9,
      sentiment: null,
    }
  }
  const ZACH: EpisodeEntity = {
    entity: { id: 'person:zach-lloyd', type: 'person', canonical_name: 'Zach Lloyd', wikidata_qid: null },
    mention_count: 2,
    first_mention_ms: 90_000,
    speaker_kind: 'guest',
    salience: 2,
    mentions: [mention('person:zach-lloyd', 7, 90_000, 'Zach'), mention('person:zach-lloyd', 9, 200_000, 'Zach Lloyd')],
  }
  const WARP: EpisodeEntity = {
    entity: { id: 'company:warp', type: 'company', canonical_name: 'Warp', wikidata_qid: null },
    mention_count: 1,
    first_mention_ms: 100_000,
    speaker_kind: 'unknown',
    salience: 1,
    mentions: [mention('company:warp', 8, 100_000, 'Warp')],
  }
  const CITED = citation({ id: 'c3', raw_label: '49:30', cited_playback_s: 2970, segment_id_hint: 42 })

  function renderSummary(content: string, props: Partial<React.ComponentProps<typeof SummaryViewer>> = {}) {
    return render(
      <MemoryRouter>
        <SummaryViewer content={content} available citations={[CITED]} entities={[ZACH, WARP]} {...props} />
      </MemoryRouter>,
    )
  }

  beforeEach(() => {
    Element.prototype.scrollIntoView = vi.fn()
  })

  it('links a name to the entity peek, borrowing the nearest citation in its block', () => {
    const onShowInTranscript = vi.fn()
    const onCite = vi.fn()
    renderSummary('Zach Lloyd, CEO of Warp, on warp speed. [49:30](?t=2970&cite=c3)', { onShowInTranscript, onCite })

    const zach = screen.getByRole('link', { name: /Zach Lloyd, Person/ })
    expect(zach).toHaveAttribute('id', 'm=person:zach-lloyd:42:summary')
    expect(zach).toHaveAttribute('href', '/entities/person/zach-lloyd')
    expect(screen.getByRole('link', { name: /Warp, Company/ })).toHaveAttribute('id', 'm=company:warp:42:summary')
    // Lowercase "warp" is a verb, not the company.
    expect(screen.getAllByRole('link')).toHaveLength(2)

    fireEvent.click(zach)
    fireEvent.click(screen.getByRole('button', { name: /Show in transcript/ }))
    expect(onShowInTranscript).toHaveBeenCalledWith(42)

    // The citation button is untouched and still fires.
    fireEvent.click(screen.getByRole('button', { name: 'Play summary citation at 49:30' }))
    expect(onCite).toHaveBeenCalledWith(CITED)
  })

  it('falls back to the first transcript mention when the block has no citation', () => {
    const onShowInTranscript = vi.fn()
    const onSeek = vi.fn()
    renderSummary('## Zach Lloyd\n\nA line about Zach Lloyd.', { onShowInTranscript, onSeek })

    // The heading is navigation, not prose: one link, from the paragraph.
    const links = screen.getAllByRole('link', { name: /Zach Lloyd, Person/ })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('id', 'm=person:zach-lloyd:7:summary')
    fireEvent.click(links[0])
    fireEvent.click(screen.getByRole('button', { name: 'Play from 1:30' }))
    expect(onSeek).toHaveBeenCalledWith(90)
    fireEvent.click(screen.getByRole('button', { name: /Show in transcript/ }))
    expect(onShowInTranscript).toHaveBeenCalledWith(7)
  })

  it('renders plain markdown when there are no entities', () => {
    const { container } = renderSummary('Zach Lloyd of Warp.', { entities: [] })
    expect(screen.queryByRole('link')).toBeNull()
    expect(container.querySelector('p')?.textContent).toBe('Zach Lloyd of Warp.')
  })
})
