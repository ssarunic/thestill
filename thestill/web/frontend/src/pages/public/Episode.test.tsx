import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Episode from './Episode'
import { usePublicEpisodeQuotes, usePublicQuoteShare } from '../../hooks/usePublicApi'
import { parseSignInFragment } from '../../utils/signIn'

vi.mock('../../hooks/usePublicApi', () => ({
  usePublicEpisode: vi.fn(() => ({
    data: {
      episode: {
        title: 'How close is a global markets crash?',
        image_url: null,
        pub_date: '2026-11-07T00:10:00Z',
        duration_formatted: '45:00',
        hosts: [],
        guests: [],
        platform_links: [],
        podcast: { slug: 'the-rest-is-money', title: 'The Rest Is Money', apple_url: null, spotify_url: null, youtube_url: null },
      },
    },
    error: null,
  })),
  usePublicSummary: vi.fn(() => ({ data: undefined, isLoading: false, isError: false })),
  usePublicEpisodeQuotes: vi.fn(() => ({ data: undefined, isLoading: false })),
  usePublicPodcastEpisodes: vi.fn(() => ({ data: undefined })),
  usePublicQuoteShare: vi.fn(() => ({ data: undefined })),
}))

describe('public episode page', () => {
  // Spec #117 — Follow beside the show name signs in and comes back here.
  it('offers Follow beside the show name, returning to this episode', () => {
    render(
      <MemoryRouter initialEntries={['/podcasts/the-rest-is-money/episodes/329-how-close-is-a-global-markets-crash']}>
        <Routes>
          <Route path="/podcasts/:podcastSlug/episodes/:episodeSlug" element={<Episode />} />
        </Routes>
      </MemoryRouter>,
    )

    const follow = screen.getByRole('link', { name: 'Follow' })
    expect(follow).toHaveAttribute('rel', 'nofollow')
    const href = follow.getAttribute('href') ?? ''
    expect(href.startsWith('/login#')).toBe(true)
    expect(parseSignInFragment(href.slice('/login'.length))).toEqual({
      next: '/podcasts/the-rest-is-money/episodes/329-how-close-is-a-global-markets-crash',
      intent: 'follow',
    })
    // It sits in the hero line with the show name.
    expect(follow.closest('.ts-eyebrow')).toHaveTextContent('The Rest Is Money')
  })

  // Spec #114 — a quote short link lands with its id in the fragment.
  describe('a shared quote (#t=…&s=…)', () => {
    const SLUGS = '/podcasts/the-rest-is-money/episodes/329-how-close-is-a-global-markets-crash'
    // The page scrolls the linked quote into view; jsdom has no such method.
    Element.prototype.scrollIntoView = vi.fn()
    const show = { slug: 'the-rest-is-money', title: 'The Rest Is Money' }
    const episode = { slug: '329-how-close-is-a-global-markets-crash', title: 'How close is a global markets crash?' }
    const inList = {
      text: 'Another excerpt of the very same turn, about someone else.',
      start_seconds: 125,
      share_id: 'bbbbbbbbbb',
      speaker: { name: 'Robert Peston', slug: 'robert-peston' },
      about: { name: 'Markets', type: 'topic', slug: 'markets', id: 'topic:markets' },
      episode: { ...episode, pub_date: null, image_url: null },
      podcast: show,
    }
    const quotesPage = {
      data: { pages: [{ quotes: [inList], total: 1, page: 1, next_page: null, linked: inList, speakers: [], mentioned: [] }] },
      isLoading: false,
    }
    const open = (hash: string) =>
      render(
        <MemoryRouter initialEntries={[`${SLUGS}${hash}`]}>
          <Routes>
            <Route path="/podcasts/:podcastSlug/episodes/:episodeSlug" element={<Episode />} />
          </Routes>
        </MemoryRouter>,
      )

    it('shows the words that were shared, not another excerpt at the same second', () => {
      vi.mocked(usePublicEpisodeQuotes).mockReturnValue(quotesPage as never)
      vi.mocked(usePublicQuoteShare).mockReturnValue({
        data: {
          share: {
            id: 'aaaaaaaaaa',
            text: 'The quote that was actually shared stands here.',
            start_seconds: 125,
            speaker: { name: 'Steph McGovern', slug: 'steph-mcgovern' },
            episode,
            podcast: show,
          },
        },
      } as never)
      open('#t=125&s=aaaaaaaaaa')
      expect(vi.mocked(usePublicQuoteShare)).toHaveBeenLastCalledWith('aaaaaaaaaa')
      const linked = screen.getByText('Linked quote').closest('figure')
      expect(linked).toHaveTextContent('The quote that was actually shared stands here.')
      expect(linked).toHaveTextContent('Steph McGovern')
      // The other excerpt is still in the list, and is not the linked one.
      expect(screen.getByText(`“${inList.text}”`).closest('figure')).not.toHaveTextContent('Linked quote')
    })

    it('scrolls to the shared quote once it is known, not to the quote at that time before', () => {
      const scroll = vi.mocked(Element.prototype.scrollIntoView)
      scroll.mockClear()
      vi.mocked(usePublicEpisodeQuotes).mockReturnValue(quotesPage as never)
      vi.mocked(usePublicQuoteShare).mockReturnValue({ data: undefined, isLoading: true } as never)
      const view = open('#t=125&s=aaaaaaaaaa')
      expect(scroll).not.toHaveBeenCalled()

      vi.mocked(usePublicQuoteShare).mockReturnValue({
        data: { share: { id: 'aaaaaaaaaa', text: 'Shared words here.', start_seconds: 125, speaker: null, episode, podcast: show } },
        isLoading: false,
      } as never)
      view.rerender(
        <MemoryRouter initialEntries={[`${SLUGS}#t=125&s=aaaaaaaaaa`]}>
          <Routes>
            <Route path="/podcasts/:podcastSlug/episodes/:episodeSlug" element={<Episode />} />
          </Routes>
        </MemoryRouter>,
      )
      expect(scroll).toHaveBeenCalledTimes(1)
      expect(scroll.mock.instances[0]).toHaveTextContent('Shared words here.')
    })

    it('names "A guest" without a link when the speaker could not be kept', () => {
      vi.mocked(usePublicEpisodeQuotes).mockReturnValue(quotesPage as never)
      vi.mocked(usePublicQuoteShare).mockReturnValue({
        data: { share: { id: 'aaaaaaaaaa', text: 'Shared words here.', start_seconds: 125, speaker: null, episode, podcast: show } },
      } as never)
      open('#t=125&s=aaaaaaaaaa')
      const linked = screen.getByText('Linked quote').closest('figure') as HTMLElement
      expect(linked).toHaveTextContent('A guest')
      expect(linked.querySelector('a.ts-quote-speaker')).toBeNull()
    })

    it('falls back to the quote at that time for a withdrawn link, another episode\u2019s id or a malformed one', () => {
      vi.mocked(usePublicEpisodeQuotes).mockReturnValue(quotesPage as never)
      vi.mocked(usePublicQuoteShare).mockReturnValue({ data: undefined, isError: true } as never)
      open('#t=125&s=aaaaaaaaaa')
      expect(screen.getByText('Linked quote').closest('figure')).toHaveTextContent(inList.text)

      vi.mocked(usePublicQuoteShare).mockClear()
      open('#t=125&s=NOT-AN-ID')
      expect(vi.mocked(usePublicQuoteShare)).toHaveBeenLastCalledWith(null)
    })
  })
})
