import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import FollowPrompt from './FollowPrompt'
import { useFollowCount, useFollowPodcast } from '../hooks/useApi'

vi.mock('../hooks/useApi', () => ({
  useFollowCount: vi.fn(),
  useFollowPodcast: vi.fn(),
}))

const mutate = vi.fn()

function setup(followCount: number | undefined) {
  ;(useFollowCount as ReturnType<typeof vi.fn>).mockReturnValue({ data: followCount })
  ;(useFollowPodcast as ReturnType<typeof vi.fn>).mockReturnValue({ mutate, isPending: false, isError: false })
  return render(
    <MemoryRouter>
      <FollowPrompt podcastSlug="prof-g" podcastTitle="The Prof G Pod" />
    </MemoryRouter>,
  )
}

describe('FollowPrompt', () => {
  beforeEach(() => {
    mutate.mockReset()
    window.localStorage.clear()
  })

  it('offers the show to a reader who follows nothing yet', () => {
    setup(0)
    expect(screen.getByText('Get every new episode of The Prof G Pod in your inbox.')).toBeInTheDocument()
  })

  it('stays out of the way of a reader who follows something, or while the count loads', () => {
    setup(3)
    expect(screen.queryByRole('region', { name: 'Follow The Prof G Pod' })).toBeNull()
    setup(undefined)
    expect(screen.queryByRole('region', { name: 'Follow The Prof G Pod' })).toBeNull()
  })

  it('follows with the episode_prompt source and confirms where episodes will go', () => {
    mutate.mockImplementation((_vars, opts) => opts.onSuccess())
    setup(0)
    fireEvent.click(screen.getByRole('button', { name: 'Follow' }))
    expect(mutate.mock.calls[0][0]).toEqual({ podcastSlug: 'prof-g', source: 'episode_prompt' })
    expect(screen.getByRole('status')).toHaveTextContent('Following The Prof G Pod. New episodes will arrive summarised in your inbox.')
    expect(screen.getByRole('link', { name: 'inbox' })).toHaveAttribute('href', '/inbox')
  })

  it('"Not now" hides it on this browser for good', () => {
    const { unmount } = setup(0)
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(screen.queryByRole('region', { name: 'Follow The Prof G Pod' })).toBeNull()
    unmount()
    setup(0)
    expect(screen.queryByRole('region', { name: 'Follow The Prof G Pod' })).toBeNull()
  })
})
