// Spec #90 — listening progress transport. The one seam the player's
// persistence layer and the Continue listening strip talk to; tests mock
// this module rather than `fetch`.
import type { ListeningSummary, ProgressAck, ProgressBody, ResumableItem } from './types'

const BASE = '/api/me/listening'

// A write's outcome. `not_found` is distinct so a stale tab snapshot can be
// dropped; every other failure keeps the write pending for a retry.
export type SendResult =
  | { kind: 'ok'; ack: ProgressAck }
  | { kind: 'not_found' }
  | { kind: 'failed' }

export async function putListening(
  episodeId: string,
  body: ProgressBody,
  { keepalive = false }: { keepalive?: boolean } = {},
): Promise<SendResult> {
  try {
    const response = await fetch(`${BASE}/${episodeId}`, {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      // Outlives the document on pagehide; ordering is by seq, not arrival.
      keepalive,
    })
    if (response.status === 404) return { kind: 'not_found' }
    if (!response.ok) return { kind: 'failed' }
    const json = await response.json()
    return {
      kind: 'ok',
      ack: { accepted: json.accepted, completed: json.completed, listening: json.listening ?? null },
    }
  } catch {
    return { kind: 'failed' }
  }
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { credentials: 'include', signal })
  if (!response.ok) throw new Error(`API error: ${response.status} ${response.statusText}`)
  return response.json()
}

export async function getListening(episodeId: string): Promise<ListeningSummary | null> {
  const json = await getJson<{ item: ListeningSummary | null }>(`${BASE}/${episodeId}`)
  return json.item ?? null
}

// The restore candidate: most recently updated unfinished episode not stopped since.
export async function getLatestListening(signal?: AbortSignal): Promise<ResumableItem | null> {
  const json = await getJson<{ item: ResumableItem | null }>(`${BASE}/latest`, signal)
  return json.item ?? null
}

export async function listInProgress(limit = 3): Promise<ResumableItem[]> {
  const json = await getJson<{ items: ResumableItem[] }>(`${BASE}?state=in_progress&limit=${limit}`)
  return json.items ?? []
}
