import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

type Callbacks = { onSuccess?: () => void; onError?: (error: Error) => void }

const follow = vi.fn()
const unfollow = vi.fn()

beforeEach(() => {
  follow.mockReset()
  unfollow.mockReset()
  ;(useFollowPodcast as ReturnType<typeof vi.fn>).mockReturnValue({ mutate: follow, isPending: false })
  ;(useUnfollowPodcast as ReturnType<typeof vi.fn>).mockReturnValue({ mutate: unfollow })
})

function renderFollow(props: Partial<Parameters<typeof EpisodeFollow>[0]> = {}) {
  const queryClient = new QueryClient()
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const view = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <EpisodeFollow podcastSlug="sample-pod" podcastTitle="Sample Pod" isFollowing={false} followable {...props} />
      </ToastProvider>
    </QueryClientProvider>,
  )
  return { ...view, invalidate }
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
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onSuccess?.())
    const { invalidate } = renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))

    expect(follow).toHaveBeenCalledWith({ podcastSlug: 'sample-pod', source: 'episode_page' }, expect.anything())
    expect(screen.getByRole('button', { name: 'Following' })).toBeDisabled()
    expect(screen.getByText(/Following Sample Pod\. New episodes will arrive summarised in your inbox and briefing\./)).toBeInTheDocument()
    // The open episodes of this show re-read their follow state.
    expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ predicate: expect.any(Function) }))
  })

  it('stays "Following" after the refetch says the show is followed', async () => {
    const user = userEvent.setup()
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onSuccess?.())
    const queryClient = new QueryClient()
    const tree = (isFollowing: boolean) => (
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <EpisodeFollow podcastSlug="sample-pod" podcastTitle="Sample Pod" isFollowing={isFollowing} followable />
        </ToastProvider>
      </QueryClientProvider>
    )
    const { rerender } = render(tree(false))
    await user.click(screen.getByRole('button', { name: 'Follow' }))

    // The episode refetch now reports the follow; the confirmation stays
    // until the reader leaves the episode (the component unmounts).
    rerender(tree(true))
    expect(screen.getByRole('button', { name: 'Following' })).toBeDisabled()
  })

  it('Undo unfollows and offers Follow again', async () => {
    const user = userEvent.setup()
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onSuccess?.())
    unfollow.mockImplementation((_slug, callbacks: Callbacks) => callbacks.onSuccess?.())
    renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))
    await user.click(screen.getByRole('button', { name: 'Undo' }))

    expect(unfollow).toHaveBeenCalledWith('sample-pod', expect.anything())
    expect(screen.getByText('Unfollowed Sample Pod')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Follow' })).toBeEnabled()
  })

  it('says why a follow failed and offers a retry', async () => {
    const user = userEvent.setup()
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onError?.(new Error('Server error')))
    renderFollow()

    await user.click(screen.getByRole('button', { name: 'Follow' }))

    expect(screen.getByText("Couldn't follow Sample Pod: Server error")).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled()
  })

  it('finishes a Follow pressed signed out on this episode, once', () => {
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onSuccess?.())
    rememberPendingFollow('sample-pod')
    const { rerender } = renderFollow()

    expect(follow).toHaveBeenCalledTimes(1)
    expect(follow).toHaveBeenCalledWith({ podcastSlug: 'sample-pod', source: 'episode_pending_follow' }, expect.anything())
    expect(screen.getByRole('button', { name: 'Following' })).toBeDisabled()
    expect(screen.getByText(/Following Sample Pod\./)).toBeInTheDocument()
    expect(window.sessionStorage.getItem('thestill.pendingFollow')).toBeNull()

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <ToastProvider>
          <EpisodeFollow podcastSlug="sample-pod" podcastTitle="Sample Pod" isFollowing followable />
        </ToastProvider>
      </QueryClientProvider>,
    )
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
    follow.mockImplementation((_vars, callbacks: Callbacks) => callbacks.onSuccess?.())
    const queryClient = new QueryClient()
    const tree = (slug: string, title: string) => (
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <EpisodeFollow podcastSlug={slug} podcastTitle={title} isFollowing={false} followable />
        </ToastProvider>
      </QueryClientProvider>
    )
    const { rerender } = render(tree('sample-pod', 'Sample Pod'))
    await user.click(screen.getByRole('button', { name: 'Follow' }))
    expect(screen.getByRole('button', { name: 'Following' })).toBeInTheDocument()

    rerender(tree('other-pod', 'Other Pod'))
    expect(screen.getByRole('button', { name: 'Follow' })).toBeEnabled()
  })
})
