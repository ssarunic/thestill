import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import BriefingIndex, { BriefingIndexSkeleton } from './BriefingIndex'
import type { BriefingEpisode, BriefingPodcastGroup } from '../api/types'

function episode(overrides: Partial<BriefingEpisode> & { id: string }): BriefingEpisode {
  return {
    title: `Episode ${overrides.id}`,
    slug: `episode-${overrides.id}`,
    pub_date: '2026-09-06T07:00:00Z',
    duration: 3480,
    duration_formatted: '58:00',
    image_url: null,
    summary_available: true,
    summary_preview: null,
    ...overrides,
  }
}

const PODCASTS: BriefingPodcastGroup[] = [
  {
    id: 'pod-a',
    title: 'The Rest Is Politics',
    slug: 'rest-is-politics',
    image_url: 'https://img/a.jpg',
    episodes: [
      episode({
        id: '1',
        title: 'Starmer under pressure',
        image_url: 'https://img/ep1.jpg',
        summary_preview: 'Rory and Alastair on the reshuffle.',
      }),
      episode({ id: '2', title: 'Question time', duration: null, pub_date: null }),
      episode({ id: '2b', title: 'Odd length', duration: 2710, pub_date: null }),
    ],
  },
  {
    id: 'pod-b',
    title: 'Hard Fork',
    slug: '',
    image_url: null,
    episodes: [episode({ id: '3', title: 'The AI bubble', slug: '' })],
  },
]

function renderIndex(podcasts = PODCASTS) {
  return render(
    <MemoryRouter initialEntries={['/briefings/b1']}>
      <BriefingIndex podcasts={podcasts} />
    </MemoryRouter>,
  )
}

describe('BriefingIndex', () => {
  it('renders nothing for an empty index', () => {
    const { container } = renderIndex([])
    expect(container).toBeEmptyDOMElement()
  })

  it('heads the index with the totals and groups episodes under their show', () => {
    renderIndex()
    expect(screen.getByRole('heading', { level: 2, name: /In this briefing/ })).toHaveTextContent(
      '4 episodes from 2 shows',
    )
    const groupA = screen.getByRole('region', { name: 'The Rest Is Politics' })
    expect(within(groupA).getByRole('link', { name: 'The Rest Is Politics' })).toHaveAttribute(
      'href',
      '/podcasts/rest-is-politics',
    )
    expect(within(groupA).getByText('3 episodes')).toBeInTheDocument()
    expect(within(groupA).getAllByRole('listitem')).toHaveLength(3)
    const groupB = screen.getByRole('region', { name: 'Hard Fork' })
    expect(within(groupB).getByText('1 episode')).toBeInTheDocument()
    // Slugless rows fall back to ids, like the inbox.
    expect(within(groupB).getByRole('link', { name: 'The AI bubble' })).toHaveAttribute(
      'href',
      '/podcasts/pod-b/episodes/3',
    )
  })

  it('renders each episode as an inbox-style row: leading artwork, title link, gist and meta', () => {
    renderIndex()
    const card = screen.getByRole('link', { name: 'Starmer under pressure' }).closest('li')!
    expect(screen.getByRole('link', { name: 'Starmer under pressure' })).toHaveAttribute(
      'href',
      '/podcasts/rest-is-politics/episodes/episode-1',
    )
    expect(within(card).getByText('Rory and Alastair on the reshuffle.')).toBeInTheDocument()
    // Node's ICU renders en-GB September as ``Sept``; browsers as ``Sep``.
    expect(within(card).getByText(/^Sun 6 Sept? · 58 min$/)).toBeInTheDocument()
    // Episode artwork wins. It leads the row like the inbox, one size up
    // (64 px) because the gist stacks under the title.
    const art = within(card).getByRole('presentation', { hidden: true })
    expect(art).toHaveAttribute('src', 'https://img/ep1.jpg')
    expect(art.className).toContain('w-16')
    expect(card.firstElementChild).toBe(art)
  })

  it('rounds the length to whole minutes on a card', () => {
    renderIndex()
    const card = screen.getByRole('link', { name: 'Odd length' }).closest('li')!
    expect(within(card).getByText('45 min')).toBeInTheDocument()
  })

  it('falls back to show artwork and omits missing gist and meta', () => {
    renderIndex()
    const card = screen.getByRole('link', { name: 'Question time' }).closest('li')!
    expect(within(card).getByRole('presentation', { hidden: true })).toHaveAttribute('src', 'https://img/a.jpg')
    expect(within(card).queryByText(/·/)).not.toBeInTheDocument()
    expect(card.querySelector('p')).toBeNull()
  })

  it('shows the placeholder glyph when neither episode nor show has artwork', () => {
    renderIndex()
    const card = screen.getByRole('link', { name: 'The AI bubble' }).closest('li')!
    expect(card.querySelector('img')).toBeNull()
    expect(card.querySelector('svg')).not.toBeNull()
  })

  it('has a skeleton the page can show while the index loads', () => {
    const { container } = render(<BriefingIndexSkeleton />)
    expect(container.firstChild).toHaveClass('animate-pulse')
  })
})
