import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FAILURE_REFETCH_ATTEMPTS, FAILURE_REFETCH_DELAY_MS, useEntityBranchRefresh } from './useApi'
import type { EpisodeTask, PipelineStage } from '../api/types'

/**
 * The entity branch runs after `summarize`, so the reader's state-diff
 * invalidation has already fired (on an empty entities result) by the time
 * mentions resolve. This hook watches the task list instead.
 *
 * Per spec #42 FM-5 the fixtures include the orderings a polite fixture
 * would skip: a task list that is already complete on first load, a retry
 * completing on the same row, a manual re-run adding a new row, and
 * navigation to an episode whose branch is long done.
 */

const EP = 'ep-uuid-1'

function task(stage: PipelineStage, status: EpisodeTask['status'], id = `${stage}-1`): EpisodeTask {
  return { id, episode_id: EP, stage, status } as EpisodeTask
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

type Props = { episodeId: string | null | undefined; tasks: EpisodeTask[] | undefined }

function render(queryClient: QueryClient, initialProps: Props) {
  return renderHook((props: Props) => useEntityBranchRefresh(props.episodeId, props.tasks), {
    wrapper: createWrapper(queryClient),
    initialProps,
  })
}

describe('useEntityBranchRefresh', () => {
  let queryClient: QueryClient
  let invalidateSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    invalidateSpy = vi.fn()
    queryClient.invalidateQueries = invalidateSpy as never
  })

  function keys(): string[] {
    return invalidateSpy.mock.calls.map(([arg]) => JSON.stringify(arg.queryKey))
  }

  it('treats the first task list as a baseline, even when the branch is already done', () => {
    const { rerender } = render(queryClient, { episodeId: EP, tasks: undefined })
    rerender({
      episodeId: EP,
      tasks: [task('resolve-entities', 'completed'), task('compute-related', 'completed')],
    })
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('refetches entities when resolve-entities completes', () => {
    const { rerender } = render(queryClient, {
      episodeId: EP,
      tasks: [task('extract-entities', 'completed'), task('resolve-entities', 'processing')],
    })
    rerender({
      episodeId: EP,
      tasks: [task('extract-entities', 'completed'), task('resolve-entities', 'completed')],
    })
    // Prefix key: every minConfidence variant.
    expect(keys()).toEqual([JSON.stringify(['episodes', EP, 'entities'])])
  })

  it('refetches the related rail when compute-related completes', () => {
    const { rerender } = render(queryClient, {
      episodeId: EP,
      tasks: [task('resolve-entities', 'completed'), task('compute-related', 'pending')],
    })
    rerender({
      episodeId: EP,
      tasks: [task('resolve-entities', 'completed'), task('compute-related', 'completed')],
    })
    expect(keys()).toEqual([JSON.stringify(['episodes', EP, 'related'])])
  })

  it('ignores stages that change nothing the reader renders', () => {
    const { rerender } = render(queryClient, { episodeId: EP, tasks: [] })
    rerender({
      episodeId: EP,
      tasks: [
        task('extract-entities', 'completed'),
        task('reindex', 'completed'),
        task('rebuild-cooccurrences', 'completed'),
        task('enrich-entities', 'completed'),
      ],
    })
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  it('invalidates once per completion, not on every later poll', () => {
    const done = [task('resolve-entities', 'completed')]
    const { rerender } = render(queryClient, { episodeId: EP, tasks: [task('resolve-entities', 'processing')] })
    rerender({ episodeId: EP, tasks: done })
    // Each 2s poll hands back a fresh array with the same rows.
    rerender({ episodeId: EP, tasks: [...done] })
    rerender({ episodeId: EP, tasks: [...done, task('reindex', 'processing')] })
    expect(invalidateSpy).toHaveBeenCalledTimes(1)
  })

  it('catches a retried task completing on the same row', () => {
    const { rerender } = render(queryClient, {
      episodeId: EP,
      tasks: [task('resolve-entities', 'retry_scheduled')],
    })
    rerender({ episodeId: EP, tasks: [task('resolve-entities', 'completed')] })
    expect(keys()).toEqual([JSON.stringify(['episodes', EP, 'entities'])])
  })

  it('catches a manual re-run completing as a new row', () => {
    const first = task('resolve-entities', 'completed', 'resolve-1')
    const { rerender } = render(queryClient, { episodeId: EP, tasks: [first] })
    rerender({ episodeId: EP, tasks: [first, task('resolve-entities', 'processing', 'resolve-2')] })
    expect(invalidateSpy).not.toHaveBeenCalled()
    rerender({ episodeId: EP, tasks: [first, task('resolve-entities', 'completed', 'resolve-2')] })
    expect(invalidateSpy).toHaveBeenCalledTimes(1)
  })

  it('treats navigation to another episode as a new baseline', () => {
    const { rerender } = render(queryClient, { episodeId: EP, tasks: [] })
    rerender({
      episodeId: 'ep-uuid-2',
      tasks: [task('resolve-entities', 'completed', 'other-resolve')],
    })
    expect(invalidateSpy).not.toHaveBeenCalled()
  })

  describe('reprocess (spec #106)', () => {
    const slugs = { podcastSlug: 'the-show', episodeSlug: 'ep-1' }
    type SlugProps = Props & { slugs?: typeof slugs }

    function renderWithSlugs(initialProps: SlugProps) {
      return renderHook((props: SlugProps) => useEntityBranchRefresh(props.episodeId, props.tasks, props.slugs), {
        wrapper: createWrapper(queryClient),
        initialProps,
      })
    }

    it('refetches the transcript and summary as clean and summarize finish', () => {
      const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('clean', 'processing')], slugs })
      rerender({ episodeId: EP, tasks: [task('clean', 'completed'), task('summarize', 'processing')], slugs })
      expect(keys()).toEqual([JSON.stringify(['episodes', 'the-show', 'ep-1', 'transcript'])])
      rerender({ episodeId: EP, tasks: [task('clean', 'completed'), task('summarize', 'completed')], slugs })
      expect(keys()).toEqual([
        JSON.stringify(['episodes', 'the-show', 'ep-1', 'transcript']),
        JSON.stringify(['episodes', 'the-show', 'ep-1', 'summary']),
      ])
    })

    it('does nothing for clean without the slugs', () => {
      const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('clean', 'processing')] })
      rerender({ episodeId: EP, tasks: [task('clean', 'completed')] })
      expect(invalidateSpy).not.toHaveBeenCalled()
    })

    describe('a failed run shows the failure banner', () => {
      // A reprocess runs on a `summarized` episode, whose 5s poll has
      // stopped; the banner reads `episode.is_failed`, so the episode itself
      // has to be refetched when the run fails.
      const episodeKey = ['episodes', 'the-show', 'ep-1']
      const episodeCalls = () =>
        invalidateSpy.mock.calls.filter(([arg]) => JSON.stringify(arg.queryKey) === JSON.stringify(episodeKey))

      beforeEach(() => {
        vi.useFakeTimers()
        queryClient.setQueryData(episodeKey, { episode: { is_failed: false } })
      })

      afterEach(() => {
        vi.useRealTimers()
      })

      it.each(['dead', 'failed'] as const)('refetches the episode, exactly, when a user-chain task turns %s', (status) => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('summarize', 'processing')], slugs })
        expect(invalidateSpy).not.toHaveBeenCalled()
        rerender({ episodeId: EP, tasks: [task('summarize', status)], slugs })
        expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: episodeKey, exact: true })
      })

      it('refetches again until is_failed shows, then stops', async () => {
        // The worker marks the task dead before it stamps the episode failed,
        // so the first refetch can still read is_failed: false.
        invalidateSpy.mockImplementation(async () => {
          if (episodeCalls().length === 2) queryClient.setQueryData(episodeKey, { episode: { is_failed: true } })
        })
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('clean', 'processing')], slugs })
        rerender({ episodeId: EP, tasks: [task('clean', 'dead')], slugs })
        expect(episodeCalls()).toHaveLength(1)

        await vi.advanceTimersByTimeAsync(FAILURE_REFETCH_DELAY_MS)
        expect(episodeCalls()).toHaveLength(2)

        await vi.advanceTimersByTimeAsync(FAILURE_REFETCH_DELAY_MS * 10)
        expect(episodeCalls()).toHaveLength(2)
      })

      it('gives up after a bounded number of attempts', async () => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('transcribe', 'processing')], slugs })
        rerender({ episodeId: EP, tasks: [task('transcribe', 'dead')], slugs })
        await vi.advanceTimersByTimeAsync(FAILURE_REFETCH_DELAY_MS * (FAILURE_REFETCH_ATTEMPTS + 5))
        expect(episodeCalls()).toHaveLength(FAILURE_REFETCH_ATTEMPTS)
      })

      it('keeps going across later task polls', async () => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('clean', 'processing')], slugs })
        rerender({ episodeId: EP, tasks: [task('clean', 'dead')], slugs })
        // A later poll with the same failure neither restarts nor cancels it.
        rerender({ episodeId: EP, tasks: [task('clean', 'dead')], slugs })
        await vi.advanceTimersByTimeAsync(FAILURE_REFETCH_DELAY_MS * (FAILURE_REFETCH_ATTEMPTS + 5))
        expect(episodeCalls()).toHaveLength(FAILURE_REFETCH_ATTEMPTS)
      })

      it('stops when the reader navigates away', async () => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('clean', 'processing')], slugs })
        rerender({ episodeId: EP, tasks: [task('clean', 'dead')], slugs })
        rerender({ episodeId: 'ep-uuid-2', tasks: [], slugs: { podcastSlug: 'the-show', episodeSlug: 'ep-2' } })
        await vi.advanceTimersByTimeAsync(FAILURE_REFETCH_DELAY_MS * (FAILURE_REFETCH_ATTEMPTS + 5))
        expect(episodeCalls()).toHaveLength(1)
      })

      it('treats a failure already on the first task list as a baseline', () => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('summarize', 'dead')], slugs })
        rerender({ episodeId: EP, tasks: [task('summarize', 'dead')], slugs })
        expect(invalidateSpy).not.toHaveBeenCalled()
      })

      it('ignores entity-branch failures, which never set the banner', () => {
        const { rerender } = renderWithSlugs({ episodeId: EP, tasks: [task('resolve-entities', 'processing')], slugs })
        rerender({ episodeId: EP, tasks: [task('resolve-entities', 'dead')], slugs })
        expect(invalidateSpy).not.toHaveBeenCalled()
      })
    })
  })
})
