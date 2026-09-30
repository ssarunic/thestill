import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEntityBranchRefresh } from './useApi'
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
})
