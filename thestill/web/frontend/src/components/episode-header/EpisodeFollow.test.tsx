import { useState } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ToastProvider } from '../Toast'
import EpisodeFollow from './EpisodeFollow'
import { useFollowPodcast, useUnfollowPodcast } from '../../hooks/useApi'
import { rememberPendingFollow } from '../../utils/signIn'

vi.mock('../../hooks/useApi', () => ({
  useFollowPodcast: vi.fn(),
  useUnfollowPodcast: vi.fn(),
}))

const follow = vi.fn()
const unfollow = vi.fn()
const UNDO = { name: 'Undo following Sample Pod' }

beforeEach(() => {
  window.sessionStorage.clear()
  follow.mockReset().mockResolvedValue(undefined)
  unfollow.mockReset().mockResolvedValue(undefined)
  ;(useFollowPodcast as ReturnType<typeof vi.fn>).mockReturnValue({ mutateAsync: follow, isPending: false })
  ;(useUnfollowPodcast as ReturnType<typeof vi.fn>).mockReturnValue({ mutateAsync: unfollow })
})

function tree(queryClient: QueryClient, props: Partial<Parameters<typeof EpisodeFollow>[0]> = {}) {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <EpisodeFollow podcastSlug="sample-pod" podcastTitle="Sample Pod" isFollowing={false} followable {...props} />
      </ToastProvider>
    </QueryClientProvider>
  )
}

function renderFollow(props: Partial<Parameters<typeof EpisodeFollow>[0]> = {}) {
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const view = render(tree(queryClient, props))
  return { ...view, queryClient, invalidate }
}

describe('EpisodeFollow', () => {
  it('offers Follow for a show the reader does not follow', () => {
    renderFollow()
    expect(screen.getByRole('button', { name: 'Follow' })).toBeEnabled()
  })

  it('renders nothing for a followed show or one that cannot be followed', () => {
    const { unmount } = renderFollow({ isFollowing: true })
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    renderFollow({ followable: false })
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('follows from the episode page, then confirms and explains with Undo', async () => {
    const user = userEvent.setup()
    const { invalidate } = renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))

    expect(follow).toHaveBeenCalledWith({ podcastSlug: 'sample-pod', source: 'episode_page' })
    expect(await screen.findByRole('button', { name: 'Following' })).toBeDisabled()
    expect(screen.getByText(/Following Sample Pod\. New episodes will arrive summarised in your inbox and briefing\./)).toBeInTheDocument()
    // Undo sits beside it in the header (reachable inside the reader's
    // modal dialog), and focus moves there from the button just pressed.
    expect(screen.getByRole('button', UNDO)).toHaveFocus()
    // The open episodes of this show re-read their follow state.
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ predicate: expect.any(Function) }))
  })

  it('stays "Following" after the refetch says the show is followed', async () => {
    const user = userEvent.setup()
    const { rerender, queryClient } = renderFollow()
    await user.click(screen.getByRole('button', { name: 'Follow' }))
    await screen.findByRole('button', { name: 'Following' })

    // The episode refetch now reports the follow; the confirmation stays
    // while this header shows the show.
    rerender(tree(queryClient, { isFollowing: true }))
    expect(screen.getByRole('button', { name: 'Following' })).toBeDisabled()
  })

  it('Undo unfollows and offers Follow again', async () => {
    const user = userEvent.setup()
    renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))
    await user.click(await screen.findByRole('button', UNDO))

    expect(unfollow).toHaveBeenCalledWith('sample-pod')
    expect(await screen.findByText('Unfollowed Sample Pod')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Follow' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Follow' })).toHaveFocus()
    expect(screen.queryByRole('button', UNDO)).toBeNull()
  })

  it('a follow that finishes after the header is gone still confirms and refreshes', async () => {
    // The reader closed the overlay or moved on mid-request.
    const user = userEvent.setup()
    let resolveFollow: () => void = () => {}
    follow.mockReturnValue(new Promise<void>((resolve) => (resolveFollow = resolve)))
    const queryClient = new QueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    function Page() {
      const [open, setOpen] = useState(true)
      return (
        <>
          <button onClick={() => setOpen(false)}>Close</button>
          {open && <EpisodeFollow podcastSlug="sample-pod" podcastTitle="Sample Pod" isFollowing={false} followable />}
        </>
      )
    }
    render(
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <Page />
        </ToastProvider>
      </QueryClientProvider>,
    )
    await user.click(screen.getByRole('button', { name: 'Follow' }))
    await user.click(screen.getByRole('button', { name: 'Close' }))

    await act(async () => resolveFollow())

    expect(await screen.findByText(/Following Sample Pod\./)).toBeInTheDocument()
    expect(invalidate).toHaveBeenCalled()
  })

  it('says when Undo failed', async () => {
    const user = userEvent.setup()
    unfollow.mockRejectedValue(new Error('Server error'))
    renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))
    await user.click(await screen.findByRole('button', UNDO))

    expect(await screen.findByText("Couldn't unfollow Sample Pod: Server error")).toBeInTheDocument()
  })

  it('says why a follow failed and offers a retry', async () => {
    const user = userEvent.setup()
    follow.mockRejectedValue(new Error('Server error'))
    renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))

    expect(await screen.findByText("Couldn't follow Sample Pod: Server error")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled()
  })

  it('finishes a Follow pressed signed out on this episode, once', async () => {
    rememberPendingFollow('sample-pod')
    const { rerender, queryClient } = renderFollow()

    expect(await screen.findByRole('button', { name: 'Following' })).toBeDisabled()
    expect(follow).toHaveBeenCalledTimes(1)
    expect(follow).toHaveBeenCalledWith({ podcastSlug: 'sample-pod', source: 'episode_pending_follow' })
    expect(screen.getByText(/Following Sample Pod\./)).toBeInTheDocument()
    // Not the reader's click: focus stays where the page put it.
    expect(screen.getByRole('button', UNDO)).not.toHaveFocus()
    expect(window.sessionStorage.getItem('thestill.pendingFollow')).toBeNull()

    rerender(tree(queryClient, { isFollowing: true }))
    expect(follow).toHaveBeenCalledTimes(1)
  })

  it('drops a pending follow for another show, or one already followed', () => {
    rememberPendingFollow('other-pod')
    const { unmount } = renderFollow()
    expect(follow).not.toHaveBeenCalled()
    expect(window.sessionStorage.getItem('thestill.pendingFollow')).toBeNull()
    unmount()

    rememberPendingFollow('sample-pod')
    renderFollow({ isFollowing: true })
    expect(follow).not.toHaveBeenCalled()
    expect(window.sessionStorage.getItem('thestill.pendingFollow')).toBeNull()
  })

  it('does not carry a confirmation over to another show', async () => {
    const user = userEvent.setup()
    const { rerender, queryClient } = renderFollow()
    await user.click(screen.getByRole('button', { name: 'Follow' }))
    expect(await screen.findByRole('button', { name: 'Following' })).toBeInTheDocument()

    rerender(tree(queryClient, { podcastSlug: 'other-pod', podcastTitle: 'Other Pod' }))
    expect(screen.getByRole('button', { name: 'Follow' })).toBeEnabled()
  })
})
