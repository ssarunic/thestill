import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useRefreshStatus } from './useApi'
import type { RefreshTaskStatus } from '../api/types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, getRefreshStatus: vi.fn() }
})

import { getRefreshStatus } from '../api/client'

const mockGetRefreshStatus = getRefreshStatus as ReturnType<typeof vi.fn>

/**
 * Issue #163 / spec #42 FM-8 — the refresh-status poll must not latch off on
 * a single quiet response.
 *
 * Per spec #42 FM-5, the fixtures script the quiet intermediate response
 * explicitly: a fixture that reads `running` on every poll until the refresh
 * ends passes against the latched implementation too.
 */

const RUNNING_POLL_MS = 1_000
const IDLE_POLL_MS = 15_000

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useRefreshStatus cadence', () => {
  let queryClient: QueryClient
  /** Mutated between ticks to script what the next poll sees. */
  let currentStatus: string

  beforeEach(() => {
    vi.useFakeTimers()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    currentStatus = 'none'
    mockGetRefreshStatus.mockImplementation(
      async () => ({ status: currentStatus }) as RefreshTaskStatus,
    )
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  function mount() {
    return renderHook(() => useRefreshStatus(), { wrapper: createWrapper(queryClient) })
  }

  // Read from the cache, not `result.current`: React Query only re-renders
  // for props read during render, so an unread `data` goes stale there.
  function cachedStatus() {
    return queryClient.getQueryData<RefreshTaskStatus>(['commands', 'refresh', 'status'])?.status
  }

  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  it('polls every second while running', async () => {
    currentStatus = 'running'
    mount()
    await tick(0)
    expect(mockGetRefreshStatus).toHaveBeenCalledTimes(1)

    await tick(RUNNING_POLL_MS)
    await tick(RUNNING_POLL_MS)
    expect(mockGetRefreshStatus).toHaveBeenCalledTimes(3)
  })

  it('keeps polling through a quiet response and picks a running refresh back up', async () => {
    currentStatus = 'running'
    mount()
    await tick(0)

    // The quiet intermediate response the latched version stopped on.
    currentStatus = 'none'
    await tick(RUNNING_POLL_MS)
    expect(cachedStatus()).toBe('none')
    const callsAtQuiet = mockGetRefreshStatus.mock.calls.length

    // A refresh started elsewhere: no local invalidation, only the poll.
    currentStatus = 'running'
    await tick(IDLE_POLL_MS)
    expect(mockGetRefreshStatus.mock.calls.length).toBe(callsAtQuiet + 1)
    expect(cachedStatus()).toBe('running')

    // Back on the fast tier.
    await tick(RUNNING_POLL_MS)
    expect(mockGetRefreshStatus.mock.calls.length).toBe(callsAtQuiet + 2)
  })

  it('stops only after four consecutive quiet ticks, and activity resets the count', async () => {
    currentStatus = 'none'
    mount()
    await tick(0) // quiet tick 1
    await tick(IDLE_POLL_MS) // 2
    await tick(IDLE_POLL_MS) // 3

    // Activity on what would have been the last quiet tick resets the count.
    currentStatus = 'running'
    await tick(IDLE_POLL_MS)
    currentStatus = 'completed'
    await tick(RUNNING_POLL_MS) // quiet tick 1 again
    const callsAfterReset = mockGetRefreshStatus.mock.calls.length

    await tick(IDLE_POLL_MS) // 2
    await tick(IDLE_POLL_MS) // 3
    await tick(IDLE_POLL_MS) // 4 — stops after this one
    expect(mockGetRefreshStatus.mock.calls.length).toBe(callsAfterReset + 3)

    await tick(IDLE_POLL_MS * 10)
    expect(mockGetRefreshStatus.mock.calls.length).toBe(callsAfterReset + 3)
  })
})
