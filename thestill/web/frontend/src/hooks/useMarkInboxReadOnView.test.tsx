import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMarkInboxReadOnView } from './useApi'
import type { InboxMarkReadResponse } from '../api/types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, markInboxRead: vi.fn() }
})

import { markInboxRead } from '../api/client'

const mockMarkInboxRead = markInboxRead as ReturnType<typeof vi.fn>

function markedResponse(marked: boolean): InboxMarkReadResponse {
  return { status: 'ok', timestamp: '2026-07-07T00:00:00Z', marked }
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useMarkInboxReadOnView', () => {
  let queryClient: QueryClient

  beforeEach(() => {
    vi.clearAllMocks()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    mockMarkInboxRead.mockResolvedValue(markedResponse(true))
  })

  it('marks the episode read once the summary is available', async () => {
    renderHook(() => useMarkInboxReadOnView('ep-1', true), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(mockMarkInboxRead).toHaveBeenCalledWith('ep-1'))
    expect(mockMarkInboxRead).toHaveBeenCalledTimes(1)
  })

  it('does not fire while the summary is unavailable', () => {
    renderHook(() => useMarkInboxReadOnView('ep-1', false), {
      wrapper: createWrapper(queryClient),
    })

    expect(mockMarkInboxRead).not.toHaveBeenCalled()
  })

  it('does not fire while the episode id is still loading', () => {
    renderHook(() => useMarkInboxReadOnView(undefined, true), {
      wrapper: createWrapper(queryClient),
    })

    expect(mockMarkInboxRead).not.toHaveBeenCalled()
  })

  it('fires only once per episode across re-renders', async () => {
    const { rerender } = renderHook(
      ({ available }: { available: boolean }) => useMarkInboxReadOnView('ep-1', available),
      { wrapper: createWrapper(queryClient), initialProps: { available: true } },
    )

    await waitFor(() => expect(mockMarkInboxRead).toHaveBeenCalledTimes(1))

    // Summary refetches / unrelated re-renders must not re-fire.
    rerender({ available: true })
    rerender({ available: false })
    rerender({ available: true })
    expect(mockMarkInboxRead).toHaveBeenCalledTimes(1)
  })

  it('fires again when the user navigates to a different episode', async () => {
    const { rerender } = renderHook(
      ({ id }: { id: string }) => useMarkInboxReadOnView(id, true),
      { wrapper: createWrapper(queryClient), initialProps: { id: 'ep-1' } },
    )

    await waitFor(() => expect(mockMarkInboxRead).toHaveBeenCalledWith('ep-1'))

    rerender({ id: 'ep-2' })
    await waitFor(() => expect(mockMarkInboxRead).toHaveBeenCalledWith('ep-2'))
    expect(mockMarkInboxRead).toHaveBeenCalledTimes(2)
  })

  it('invalidates inbox queries when a row actually transitioned', async () => {
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderHook(() => useMarkInboxReadOnView('ep-1', true), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['inbox'] }),
    )
  })

  it('skips invalidation when nothing changed server-side', async () => {
    mockMarkInboxRead.mockResolvedValue(markedResponse(false))
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderHook(() => useMarkInboxReadOnView('ep-1', true), {
      wrapper: createWrapper(queryClient),
    })

    await waitFor(() => expect(mockMarkInboxRead).toHaveBeenCalledTimes(1))
    expect(invalidateSpy).not.toHaveBeenCalled()
  })
})
