import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('./client', () => ({
  addPodcast: vi.fn(),
  getAddPodcastStatus: vi.fn(),
}))

import { addPodcast, getAddPodcastStatus } from './client'
import { ApiStatusError } from './errors'
import { followPodcast, resetFollowQueueForTests } from './followPodcast'

const mockAddPodcast = addPodcast as ReturnType<typeof vi.fn>
const mockGetAddPodcastStatus = getAddPodcastStatus as ReturnType<typeof vi.fn>

const STARTED = { status: 'started', message: 'started', task_type: 'add_podcast' }

describe('followPodcast', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    resetFollowQueueForTests()
    mockAddPodcast.mockResolvedValue(STARTED)
    mockGetAddPodcastStatus.mockResolvedValue({ status: 'completed', error: null })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('waits out a 409 from an add already running, then follows', async () => {
    mockAddPodcast
      .mockRejectedValueOnce(new ApiStatusError('Add podcast already in progress', 409))
      .mockResolvedValueOnce(STARTED)

    const done = followPodcast('https://example.com/a', 'top_podcasts')
    await vi.runAllTimersAsync()
    await expect(done).resolves.toBeUndefined()
    expect(mockAddPodcast).toHaveBeenCalledTimes(2)
  })

  it('fails straight away on any other error', async () => {
    mockAddPodcast.mockRejectedValueOnce(new ApiStatusError('Too many requests', 429))

    await expect(followPodcast('https://example.com/a', 'top_podcasts')).rejects.toThrow('Too many requests')
    expect(mockAddPodcast).toHaveBeenCalledTimes(1)
  })

  it('reports a background add that failed', async () => {
    mockGetAddPodcastStatus.mockResolvedValueOnce({ status: 'failed', error: 'No public feed' })

    await expect(followPodcast('https://example.com/a', 'top_podcasts')).rejects.toThrow('No public feed')
  })

  it('starts the second follow only after the first finished', async () => {
    mockGetAddPodcastStatus
      .mockResolvedValueOnce({ status: 'running', error: null })
      .mockResolvedValue({ status: 'completed', error: null })

    const first = followPodcast('https://example.com/a', 'top_podcasts')
    const second = followPodcast('https://example.com/b', 'top_podcasts')
    await vi.advanceTimersByTimeAsync(0)
    // The first add is still running, so the second POST hasn't gone out.
    expect(mockAddPodcast).toHaveBeenCalledTimes(1)

    await vi.runAllTimersAsync()
    await Promise.all([first, second])
    expect(mockAddPodcast.mock.calls.map(([req]) => req.url)).toEqual([
      'https://example.com/a',
      'https://example.com/b',
    ])
  })

  it('keeps the queue moving after a failed follow', async () => {
    mockAddPodcast.mockRejectedValueOnce(new ApiStatusError('Bad feed', 400))

    const first = followPodcast('https://example.com/a', 'top_podcasts')
    const second = followPodcast('https://example.com/b', 'top_podcasts')
    await expect(first).rejects.toThrow('Bad feed')
    await expect(second).resolves.toBeUndefined()
  })
})
