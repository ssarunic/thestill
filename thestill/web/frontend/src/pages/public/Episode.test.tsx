import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Episode from './Episode'
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
})
