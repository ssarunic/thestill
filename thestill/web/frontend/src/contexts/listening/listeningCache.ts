// Spec #90 — client cache of listening positions, keyed by episode id.
//
// Filled from read payloads (inbox rows, episode detail, `latest`) and from
// the player's own write acks. Every entry is server-stamped, so "newer"
// is a comparison of server `updated_at` values — never of client clocks.
// `play()` reads it synchronously (Safari only honours the first play()
// inside the click handler); surfaces subscribe per episode, so a tick for
// one episode re-renders nothing else.
import type { ListeningEvent, ListeningSummary } from '../../api/types'

type Listener = () => void

// undefined = unknown, null = known never played.
const entries = new Map<string, ListeningSummary | null>()
const listeners = new Map<string, Set<Listener>>()

export interface ListeningWrite {
  episodeId: string
  event: ListeningEvent
  completed: boolean
}
const writeListeners = new Set<(write: ListeningWrite) => void>()

function updatedAt(summary: ListeningSummary): number {
  const t = Date.parse(summary.updated_at)
  return Number.isFinite(t) ? t : 0
}

/** The summary the server stamped later; `a` on a tie. */
export function newerSummary(a: ListeningSummary, b: ListeningSummary): ListeningSummary {
  return updatedAt(b) > updatedAt(a) ? b : a
}

function notify(episodeId: string) {
  listeners.get(episodeId)?.forEach((cb) => cb())
}

export const listeningCache = {
  get(episodeId: string): ListeningSummary | null | undefined {
    return entries.get(episodeId)
  },

  // Keep whichever summary the server stamped later. A null ("never
  // played") only fills an unknown entry: it can never erase a position.
  seed(episodeId: string, summary: ListeningSummary | null | undefined) {
    if (summary === undefined) return
    const current = entries.get(episodeId)
    if (summary === null) {
      if (current !== undefined) return
    } else if (current && newerSummary(current, summary) === current) {
      return
    }
    if (current === summary) return
    entries.set(episodeId, summary)
    notify(episodeId)
  },

  subscribe(episodeId: string, cb: Listener): () => void {
    let set = listeners.get(episodeId)
    if (!set) {
      set = new Set()
      listeners.set(episodeId, set)
    }
    set.add(cb)
    return () => {
      set?.delete(cb)
      if (set?.size === 0) listeners.delete(episodeId)
    }
  },

  // A different user signed in: nothing cached belongs to them.
  clear() {
    const ids = [...entries.keys()]
    entries.clear()
    ids.forEach(notify)
  },
}

// Server-acknowledged writes, for the React Query bridge (the player itself
// stays free of React Query so it renders without a QueryClientProvider).
export const listeningWrites = {
  emit(write: ListeningWrite) {
    writeListeners.forEach((cb) => cb(write))
  },
  subscribe(cb: (write: ListeningWrite) => void): () => void {
    writeListeners.add(cb)
    return () => writeListeners.delete(cb)
  },
}
