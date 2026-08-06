import { QueryClient } from '@tanstack/react-query'

/**
 * App-wide QueryClient defaults (spec #69 Phase 3).
 *
 * There is deliberately NO default `refetchInterval`: the old app-wide 5s
 * poll meant every mounted query — dashboards, searches, entity pages,
 * every loaded page of every infinite list — re-hit its endpoint every 5
 * seconds for as long as it was on screen. Hooks that genuinely need live
 * data declare their own interval:
 *
 *   - `useEpisode` — the spec #68 live-reader clock (5s while unsettled,
 *     stops on settle).
 *   - `useRefreshStatus` / `useAddPodcastStatus` / `usePipelineTaskStatus`
 *     — poll while running, stop on terminal status.
 *   - `useEpisodeTasks` — 2s → 15s → 60s → stop cadence.
 *   - `useQueueTasks` — 5s/15s operator queue view.
 *   - `useInbox` / `useInboxInfinite` — caller-supplied conditional poll
 *     while an item is mid-pipeline.
 *
 * Everything else is fetch-on-mount + invalidate-on-mutation, with a 60s
 * `staleTime` so tab-focus revalidation stays cheap. Do not re-add a
 * default `refetchInterval` here — `src/queryClient.test.ts` pins this.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,
        retry: 1,
      },
    },
  })
}
