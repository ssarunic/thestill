import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { parseSignInFragment } from '../../utils/signIn'
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

  it('omits the speaker on the speaker’s own page', () => {
    render(
      <MemoryRouter>
        <QuoteCard quote={quote} showSpeaker={false} />
      </MemoryRouter>,
    )
    expect(screen.queryByRole('link', { name: 'Scott Galloway' })).toBeNull()
  })

  // Spec #120 — "Play from" signs in and plays in the signed-in reader;
  // nothing on a quote leads off thestill.
  it('offers Play from in place of the timestamp, through sign-in to the reader at that moment', () => {
    render(
      <MemoryRouter>
        <QuoteCard quote={quote} play />
      </MemoryRouter>,
    )
    const play = screen.getByRole('link', { name: 'Play from 3 minutes 58 seconds' })
    expect(play).toHaveTextContent('Play from 3:58')
    expect(play).toHaveAttribute('rel', 'nofollow')
    const href = play.getAttribute('href') ?? ''
    expect(href.startsWith('/login#')).toBe(true)
    expect(parseSignInFragment(href.slice('/login'.length))).toEqual({
      next: '/podcasts/prof-g/episodes/portfolio?view=transcript&t=238',
      intent: 'play',
    })
    expect(screen.queryByRole('link', { name: /at 3 minutes/ })).toBeNull()
    for (const link of screen.getAllByRole('link')) {
      expect(link.getAttribute('href') ?? '').toMatch(/^\//)
    }
  })
})

describe('QuoteCard share (spec #114)', () => {
  const writeText = vi.fn(async () => {})
  const share = (q: PublicQuote) => {
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    render(
      <MemoryRouter>
        <QuoteCard quote={q} />
      </MemoryRouter>,
    )
    fireEvent.click(screen.getByRole('button', { name: q.share_id ? 'Share this quote' : 'Copy link to this moment' }))
  }
  const fetchMock = vi.fn(async () => new Response(null, { status: 204 }))
  afterEach(() => {
    writeText.mockClear()
    fetchMock.mockClear()
    vi.unstubAllGlobals()
  })

  it('hands out the short link, which previews as the quote, and reports the share without the URL', async () => {
    share({ ...quote, share_id: 'k7m2xq3dpa' })
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/q/k7m2xq3dpa`))
    // Counted through our own server (the public site has no analytics
    // client): how it was shared, never the quote or its link.
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/public/quote-shared',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ method: 'link' }) }),
    )
    expect(await screen.findByRole('button', { name: 'Link copied' })).toBeInTheDocument()
  })

  it('keeps the link to the moment for a quote with no short link', async () => {
    share({ ...quote, share_id: null })
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/podcasts/prof-g/episodes/portfolio#t=238`),
    )
  })
})
