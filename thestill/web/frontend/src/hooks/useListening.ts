// Spec #90 — listening progress for surfaces (inbox rows, episode page,
// Continue listening strip).
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listInProgress } from '../api/listening'
import type { ListeningSummary } from '../api/types'
import { listeningCache, listeningWrites } from '../contexts/listening/listeningCache'

function stamp(summary: ListeningSummary): number {
  const t = Date.parse(summary.updated_at)
  return Number.isFinite(t) ? t : 0
}

/**
 * The episode's progress: the payload's value, or the player's newer write.
 * Seeds the shared cache from the payload, so a later play() of this
 * episode resumes without a network round trip, and re-renders when the
 * player writes progress for it.
 */
export function useResolvedListening(
  episodeId: string | undefined,
  fromPayload: ListeningSummary | null | undefined,
): ListeningSummary | null {
  useEffect(() => {
    if (episodeId) listeningCache.seed(episodeId, fromPayload)
  }, [episodeId, fromPayload])

  const subscribe = useCallback(
    (cb: () => void) => (episodeId ? listeningCache.subscribe(episodeId, cb) : () => {}),
    [episodeId],
  )
  const cached = useSyncExternalStore(subscribe, () => (episodeId ? listeningCache.get(episodeId) : undefined))

  if (cached && fromPayload) return stamp(cached) >= stamp(fromPayload) ? cached : fromPayload
  return cached ?? fromPayload ?? null
}

/**
 * Refresh query data a listening write invalidates. Lives outside the
 * player (which renders without a QueryClientProvider in its tests):
 * pauses, stops and marks refresh the Continue listening list; a
 * completion also flips the inbox row from unread to read server-side.
 */
export function useListeningQuerySync(): void {
  const queryClient = useQueryClient()
  useEffect(
    () =>
      listeningWrites.subscribe(({ event, completed }) => {
        if (event === 'tick' || event === 'seek') return
        void queryClient.invalidateQueries({ queryKey: ['listening'] })
        if (completed) void queryClient.invalidateQueries({ queryKey: ['inbox'] })
      }),
    [queryClient],
  )
}

/** Mounts {@link useListeningQuerySync} inside the app's QueryClientProvider. */
export function ListeningQuerySync(): null {
  useListeningQuerySync()
  return null
}

/** Spec #90 Continue listening — unfinished episodes, newest first. */
export function useInProgressListening(limit = 3, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['listening', 'in-progress', limit],
    queryFn: () => listInProgress(limit),
    enabled,
    staleTime: 15_000,
  })
}
