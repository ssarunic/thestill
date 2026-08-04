import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEpisodeTasks } from './useApi'
import type { EpisodeTasksResponse, PipelineStage } from '../api/types'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, getEpisodeTasks: vi.fn() }
})

import { getEpisodeTasks } from '../api/client'

const mockGetEpisodeTasks = getEpisodeTasks as ReturnType<typeof vi.fn>

/**
 * Spec #68 D2 — the task poll must survive the moments when the task list
 * is legitimately empty while the pipeline is still running.
 *
 * `TaskWorker` marks a task complete and only *then* enqueues its successor
 * (two separate DB writes), so an empty response mid-pipeline is normal. The
 * previous implementation treated it as "done" and stopped polling forever.
 *
 * Per spec #42 FM-5, every fixture here scripts those empty responses
 * explicitly — a fixture that always has exactly one active task until the
 * pipeline ends passes against the broken implementation.
 */

const EPISODE_ID = 'ep-uuid-1'

const ACTIVE_POLL_MS = 2_000
const IDLE_POLL_MS = 15_000
const PROBE_POLL_MS = 60_000

type Status = 'pending' | 'processing' | 'completed' | 'failed' | 'retry_scheduled'

function task(stage: PipelineStage, status: Status) {
  return {
    id: `${stage}-${status}`,
    episode_id: EPISODE_ID,
    stage,
    status,
    priority: 0,
    error_message: null,
    created_at: '2026-08-03T00:00:00+00:00',
    updated_at: '2026-08-03T00:00:00+00:00',
    started_at: null,
    completed_at: null,
    retry_count: 0,
    max_retries: 3,
  }
}

function response(tasks: ReturnType<typeof task>[]): EpisodeTasksResponse {
  return { episode_id: EPISODE_ID, tasks } as EpisodeTasksResponse
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useEpisodeTasks cadence', () => {
  let queryClient: QueryClient
  /** Mutated between ticks to script what the next poll sees. */
  let currentTasks: ReturnType<typeof task>[]

  beforeEach(() => {
    vi.useFakeTimers()
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    currentTasks = []
    mockGetEpisodeTasks.mockImplementation(async () => response(currentTasks))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  function mount({ contentTerminal = false } = {}) {
    return renderHook(() => useEpisodeTasks(EPISODE_ID, { contentTerminal }), {
      wrapper: createWrapper(queryClient),
    })
  }

  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  /** Flush the mount fetch without advancing a poll interval. */
  async function settle() {
    await tick(0)
  }

  it('keeps polling after a response with zero active tasks', async () => {
    // THE regression test. On the previous implementation this stops at one
    // call: an empty list latched `refetchInterval` to false, and nothing
    // restarted it for the remainder of the pipeline.
    currentTasks = []
    mount()
    await settle()
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(1)

    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(2)
  })

  it('recovers the fast cadence when the successor task appears', async () => {
    // The complete-then-enqueue window: transcribe is done, clean is not yet
    // enqueued, then it lands. Polling must return to the active cadence.
    currentTasks = [task('transcribe', 'processing')]
    mount()
    await settle()

    currentTasks = [] // the gap
    await tick(ACTIVE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(2)

    currentTasks = [task('clean', 'pending')] // successor enqueued
    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(3)

    const afterRecovery = mockGetEpisodeTasks.mock.calls.length
    await tick(ACTIVE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(afterRecovery + 1)
  })

  it('does not stop inside the entity-branch gap', async () => {
    // summarize completed, extract-entities not yet enqueued. Stopping here
    // would freeze EntityBranchProgress at empty — F1 relocated.
    currentTasks = [task('summarize', 'processing')]
    mount()
    await settle()

    currentTasks = [task('summarize', 'completed')]
    await tick(ACTIVE_POLL_MS)
    const beforeGap = mockGetEpisodeTasks.mock.calls.length

    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(beforeGap + 1)

    currentTasks = [task('summarize', 'completed'), task('extract-entities', 'pending')]
    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(beforeGap + 2)
  })

  it('stops after the grace period on a terminal episode, and not before', async () => {
    currentTasks = []
    mount({ contentTerminal: true })
    await settle()

    // Ticks 1-3 of the grace period: still polling.
    await tick(IDLE_POLL_MS)
    await tick(IDLE_POLL_MS)
    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(4)

    // The 4th consecutive idle tick ends it.
    const settled = mockGetEpisodeTasks.mock.calls.length
    await tick(IDLE_POLL_MS * 10)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(settled)
  })

  it('resets the idle counter when a task reappears', async () => {
    // Two idle ticks, then activity, then two more idle ticks: the counter
    // restarted, so this must still be polling.
    currentTasks = []
    mount()
    await settle()

    await tick(IDLE_POLL_MS)
    await tick(IDLE_POLL_MS)

    currentTasks = [task('download', 'pending')]
    await tick(IDLE_POLL_MS)

    currentTasks = []
    await tick(ACTIVE_POLL_MS)
    const afterReset = mockGetEpisodeTasks.mock.calls.length

    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(afterReset + 1)
  })

  it('stops polling a terminal legacy episode that never ran the entity branch', async () => {
    // Summarized before spec #28 shipped: user-chain rows only, no entity
    // rows will ever appear. Keying termination on a terminal entity task
    // would poll these forever.
    currentTasks = [
      task('transcribe', 'completed'),
      task('clean', 'completed'),
      task('summarize', 'completed'),
    ]
    mount({ contentTerminal: true })
    await settle()

    await tick(IDLE_POLL_MS * 4)
    const settled = mockGetEpisodeTasks.mock.calls.length

    await tick(IDLE_POLL_MS * 10)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(settled)
  })

  it('keeps a slow probe on a non-terminal episode so external work is discovered', async () => {
    // An externally started stage (scheduler, CLI, another client) produces no
    // local signal at all until it *completes* — episode state changes on
    // completion, not on start. Without this probe the reader would show
    // "nothing running" for the whole duration of someone else's transcribe.
    currentTasks = []
    mount({ contentTerminal: false })
    await settle()

    await tick(IDLE_POLL_MS * 4)
    const afterGrace = mockGetEpisodeTasks.mock.calls.length

    // Silent at the idle cadence...
    await tick(IDLE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(afterGrace)

    // ...but the probe still fires within a probe window, and finds the
    // externally queued work. Asserted as "at least one" rather than an exact
    // offset: the probe counts from the last fetch, so pinning the boundary
    // here would encode arithmetic that breaks whenever the cadence changes.
    currentTasks = [task('transcribe', 'processing')]
    await tick(PROBE_POLL_MS)
    const afterProbe = mockGetEpisodeTasks.mock.calls.length
    expect(afterProbe).toBeGreaterThan(afterGrace)

    // Having found it, the fast cadence resumes.
    await tick(ACTIVE_POLL_MS)
    expect(mockGetEpisodeTasks.mock.calls.length).toBeGreaterThan(afterProbe)
  })

  it('treats retry_scheduled as active', async () => {
    // The task is coming back on its own; the chain has not gone quiet.
    currentTasks = [task('transcribe', 'retry_scheduled')]
    mount()
    await settle()

    await tick(ACTIVE_POLL_MS)
    expect(mockGetEpisodeTasks).toHaveBeenCalledTimes(2)

    await tick(ACTIVE_POLL_MS * 20)
    expect(mockGetEpisodeTasks.mock.calls.length).toBeGreaterThan(3)
  })
})
