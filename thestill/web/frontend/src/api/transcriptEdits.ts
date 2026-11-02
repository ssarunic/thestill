// Spec #110 — the transcript editor's API calls (/api/episodes/{id}/transcript/edits,
// ``transcript.edit`` permission). The transcript itself comes from
// ``getEpisodeTranscript``; these carry the op log and writes.
import { fetchJson, queryString } from './http'
import type {
  TranscriptEditOpInput,
  TranscriptEditorState,
  TranscriptEditsApplyResponse,
  TranscriptEditsFinishResponse,
  TranscriptPerson,
} from './types'

const BASE = '/api/episodes'

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

/** The server's refusal, parsed so the editor can rebase on a stale base. */
export interface TranscriptEditRefusal {
  error: 'stale_base_seq' | 'busy' | 'edit_rejected' | 'invalid' | 'not_found' | string
  message: string
  last_seq?: number
  conflict?: { reason: string; detail: Record<string, unknown> }
}

export class TranscriptEditError extends Error {
  readonly status: number
  readonly refusal: TranscriptEditRefusal | null

  constructor(status: number, refusal: TranscriptEditRefusal | null, message: string) {
    super(message)
    this.name = 'TranscriptEditError'
    this.status = status
    this.refusal = refusal
  }

  get isStale(): boolean {
    return this.refusal?.error === 'stale_base_seq'
  }
}

async function editorFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'include',
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body && typeof body === 'object' ? (body as { detail?: unknown }).detail : null
    const refusal =
      detail && typeof detail === 'object' && 'error' in (detail as object)
        ? (detail as TranscriptEditRefusal)
        : null
    const message = refusal?.message ?? (typeof detail === 'string' ? detail : `Request failed (${response.status})`)
    throw new TranscriptEditError(response.status, refusal, message)
  }
  return response.json()
}

export const getTranscriptEditorState = (episodeId: string) =>
  fetchJson<TranscriptEditorState>(`${BASE}/${episodeId}/transcript/edits`)

export const applyTranscriptEdits = (
  episodeId: string,
  body: { base_seq: number; session_id: string; ops: TranscriptEditOpInput[]; report_id?: string | null },
) => editorFetch<TranscriptEditsApplyResponse>(`${BASE}/${episodeId}/transcript/edits`, json('POST', body))

export const revertTranscriptEdit = (
  episodeId: string,
  editId: string,
  body: { base_seq: number; session_id: string },
) =>
  editorFetch<TranscriptEditsApplyResponse>(
    `${BASE}/${episodeId}/transcript/edits/${encodeURIComponent(editId)}/revert`,
    json('POST', body),
  )

export const finishTranscriptEdits = (episodeId: string, sessionId: string) =>
  editorFetch<TranscriptEditsFinishResponse>(
    `${BASE}/${episodeId}/transcript/edits/finish`,
    json('POST', { session_id: sessionId }),
  )

export const saveSpeakerAsHost = (episodeId: string, body: { name: string; entity_id?: string | null }) =>
  editorFetch<{ hosts: string[] }>(`${BASE}/${episodeId}/transcript/edits/save-host`, json('POST', body))

export const searchTranscriptPeople = (episodeId: string, q: string, signal?: AbortSignal) =>
  fetchJson<{ people: TranscriptPerson[] }>(`${BASE}/${episodeId}/transcript/edits/people${queryString({ q })}`, {
    signal,
  })
