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

const STARTED = { status: 'started', message: 'started', task_type: 'add_podcast', task_id: 'task-a' }

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

  it("polls this add's own status by task_id", async () => {
    await followPodcast('https://example.com/a', 'top_podcasts')
    expect(mockGetAddPodcastStatus).toHaveBeenCalledWith('task-a')
  })

  it('fails when the add completed but the follow did not', async () => {
    mockGetAddPodcastStatus.mockResolvedValueOnce({ status: 'completed', error: null, result: { is_following: false } })

    await expect(followPodcast('https://example.com/a', 'top_podcasts')).rejects.toThrow('following it failed')
  })

  it('gives the add its own time after a long wait for the slot', async () => {
    // 59 s of 409s, then the slot frees; the add itself then takes 5 s.
    const busy = new ApiStatusError('Add podcast already in progress', 409)
    mockAddPodcast.mockImplementation(async () => {
      if (Date.now() - start < 59_000) throw busy
      return STARTED
    })
    mockGetAddPodcastStatus.mockImplementation(async () =>
      Date.now() - start < 64_000 ? { status: 'running', error: null } : { status: 'completed', error: null },
    )
    const start = Date.now()

    const done = followPodcast('https://example.com/a', 'top_podcasts')
    const outcome = done.then(() => 'followed', (err: Error) => err.message)
    await vi.runAllTimersAsync()
    expect(await outcome).toBe('followed')
  })

  it('gives up waiting for the slot after a minute', async () => {
    mockAddPodcast.mockRejectedValue(new ApiStatusError('Add podcast already in progress', 409))

    const done = followPodcast('https://example.com/a', 'top_podcasts')
    const outcome = done.then(() => 'followed', (err: Error) => err.message)
    await vi.runAllTimersAsync()
    expect(await outcome).toMatch(/Another podcast is still being added/)
  })

  it('does not let a stalled request wedge the queue', async () => {
    mockGetAddPodcastStatus.mockImplementationOnce(() => new Promise(() => {}))

    const first = followPodcast('https://example.com/a', 'top_podcasts')
    const firstOutcome = first.then(() => 'followed', (err: Error) => err.message)
    const second = followPodcast('https://example.com/b', 'top_podcasts')
    await vi.runAllTimersAsync()
    expect(await firstOutcome).toMatch(/not responding/)
    await expect(second).resolves.toBeUndefined()
  })

  it('keeps the queue moving after a failed follow', async () => {
    mockAddPodcast.mockRejectedValueOnce(new ApiStatusError('Bad feed', 400))

    const first = followPodcast('https://example.com/a', 'top_podcasts')
    const second = followPodcast('https://example.com/b', 'top_podcasts')
    await expect(first).rejects.toThrow('Bad feed')
    await expect(second).resolves.toBeUndefined()
  })
})
