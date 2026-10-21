import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { PublicQuote } from '../../api/public'
import { QuoteCard } from './ui'

const quote: PublicQuote = {
  text: 'The economy is an iceberg where 90 to 95 percent of it is below the surface.',
  start_seconds: 238,
  speaker: { name: 'Scott Galloway', slug: 'scott-galloway' },
  about: { name: 'Economy', type: 'topic', slug: 'economy', id: 'topic:economy' },
  episode: { slug: 'portfolio', title: 'Protecting Your Portfolio', pub_date: '2026-09-28T08:00:00Z', image_url: null },
  podcast: { slug: 'prof-g', title: 'The Prof G Pod' },
}

describe('QuoteCard (spec #94)', () => {
  it('always shows attribution and links the minute to the episode with #t= (the reader deep-link parameter, kept out of the crawlable URL)', () => {
    render(
      <MemoryRouter>
        <QuoteCard quote={quote} />
      </MemoryRouter>,
    )
    expect(screen.getByText(`“${quote.text}”`)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Scott Galloway' })).toHaveAttribute('href', '/entities/person/scott-galloway')
    expect(screen.getByRole('link', { name: 'The Prof G Pod' })).toHaveAttribute('href', '/podcasts/prof-g')
    const minute = screen.getByRole('link', { name: 'at 3 minutes 58 seconds' })
    expect(minute).toHaveAttribute('href', '/podcasts/prof-g/episodes/portfolio#t=238')
    expect(minute).toHaveTextContent('at 3:58')
  })

  it('omits the speaker on the speaker’s own page and adds the YouTube link only when given', () => {
    render(
      <MemoryRouter>
        <QuoteCard quote={quote} showSpeaker={false} youtubeUrl="https://youtube.com/watch?v=abc" />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('link', { name: 'Scott Galloway' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Hear it on YouTube' })).toHaveAttribute('href', 'https://youtube.com/watch?v=abc&t=238')
  })
})
