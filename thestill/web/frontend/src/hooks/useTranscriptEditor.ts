// Spec #110 — the transcript editor's state: the op log, the speaker panel,
// writes against ``base_seq``, undo / redo as revert rows, and the session
// whose Finish queues the entity refresh. One owner (EpisodeReader) mounts
// it; the viewer and the panel receive callbacks.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  TranscriptEditError,
  applyTranscriptEdits,
  finishTranscriptEdits,
  getTranscriptEditorState,
  revertTranscriptEdit,
  saveSpeakerAsHost,
} from '../api/transcriptEdits'
import type {
  AnnotatedSegment,
  SegmentAnchorInput,
  SegmentKind,
  TranscriptEdit,
  TranscriptEditOpInput,
  TranscriptEditorState,
  TranscriptEditsFinishResponse,
} from '../api/types'

export type SaveStatus = 'saved' | 'saving' | 'error' | 'stale'

export interface EditorQueryKeys {
  podcastSlug: string
  episodeSlug: string
}

/** The anchor the server validates: the segment's durable ids plus its text as shown. */
export function segmentAnchor(segment: AnnotatedSegment): SegmentAnchorInput {
  return {
    source_segment_ids: segment.source_segment_ids,
    source_word_span: segment.source_word_span,
    start_s: segment.start,
    end_s: segment.end,
    text: segment.text,
  }
}

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

export const editorStateKey = (episodeId: string | null | undefined) => ['episodes', episodeId, 'transcript-edits'] as const

/**
 * Read-only use: the stale-summary badge and the "edited" marker need the
 * state without an editing session. ``enabled`` gates the request on the
 * caller holding ``transcript.edit``, so readers never fetch it.
 */
export function useTranscriptEditorState(episodeId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: editorStateKey(episodeId),
    queryFn: () => getTranscriptEditorState(episodeId!),
    enabled: enabled && !!episodeId,
    staleTime: 30_000,
  })
}

export interface TranscriptEditorApi {
  state: TranscriptEditorState | undefined
  isLoading: boolean
  loadError: Error | null
  sessionId: string
  saveStatus: SaveStatus
  saveError: string | null
  /** Ops this session wrote that are live (undo pops the last one). */
  canUndo: boolean
  /** Reverts this session wrote that are live (redo reverts the last one). */
  canRedo: boolean
  pending: boolean
  apply: (ops: TranscriptEditOpInput[], reportId?: string | null) => Promise<TranscriptEdit[] | null>
  undo: () => Promise<void>
  redo: () => Promise<void>
  revert: (editId: string) => Promise<void>
  finish: () => Promise<TranscriptEditsFinishResponse | null>
  saveAsHost: (name: string, entityId: string | null) => Promise<string[] | null>
  // Convenience builders for the viewer's affordances.
  renameLabel: (label: string, name: string, entityId: string | null) => Promise<void>
  mergeLabels: (label: string, intoLabel: string) => Promise<void>
  setSegmentSpeaker: (segment: AnnotatedSegment, target: { label: string } | { name: string; entityId: string | null }) => Promise<void>
  setSegmentText: (segment: AnnotatedSegment, newText: string) => Promise<void>
  setSegmentKind: (segment: AnnotatedSegment, kind: SegmentKind) => Promise<void>
  splitSegment: (segment: AnnotatedSegment, textWordIndex: number) => Promise<void>
  mergeSegments: (first: AnnotatedSegment, second: AnnotatedSegment) => Promise<void>
}

export function useTranscriptEditor(
  episodeId: string | null | undefined,
  keys: EditorQueryKeys | undefined,
  { enabled }: { enabled: boolean },
): TranscriptEditorApi {
  const queryClient = useQueryClient()
  const sessionId = useMemo(() => newSessionId(), [])
  const query = useTranscriptEditorState(episodeId, enabled)
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('saved')
  const [saveError, setSaveError] = useState<string | null>(null)
  // The last seq this editor saw. Reads follow the query; writes advance it
  // so a burst of edits never races the refetch.
  const baseSeqRef = useRef(0)
  useEffect(() => {
    if (query.data) baseSeqRef.current = Math.max(baseSeqRef.current, query.data.last_seq)
  }, [query.data])

  const invalidate = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: editorStateKey(episodeId) }),
      keys
        ? queryClient.invalidateQueries({ queryKey: ['episodes', keys.podcastSlug, keys.episodeSlug, 'transcript'] })
        : Promise.resolve(),
      keys
        ? queryClient.invalidateQueries({ queryKey: ['episodes', keys.podcastSlug, keys.episodeSlug, 'transcript', 'words'] })
        : Promise.resolve(),
    ])
  }, [queryClient, episodeId, keys])

  const write = useMutation({
    mutationFn: async (run: () => Promise<{ applied: TranscriptEdit[]; last_seq: number }>) => run(),
  })

  const run = useCallback(
    async (
      operation: (baseSeq: number) => Promise<{ applied: TranscriptEdit[]; last_seq: number }>,
    ): Promise<TranscriptEdit[] | null> => {
      if (!episodeId) return null
      setSaveStatus('saving')
      setSaveError(null)
      try {
        const result = await write.mutateAsync(() => operation(baseSeqRef.current))
        baseSeqRef.current = result.last_seq
        setSaveStatus('saved')
        await invalidate()
        return result.applied
      } catch (error) {
        if (error instanceof TranscriptEditError && error.isStale) {
          // Another editor moved the log: reload and let the admin redo the
          // change against what is there now (spec #110 §2).
          baseSeqRef.current = error.refusal?.last_seq ?? baseSeqRef.current
          setSaveStatus('stale')
          setSaveError('The transcript changed elsewhere. It has been reloaded; please repeat the edit.')
          await invalidate()
          return null
        }
        setSaveStatus('error')
        setSaveError(error instanceof Error ? error.message : 'Could not save')
        return null
      }
    },
    [episodeId, write, invalidate],
  )

  const apply = useCallback(
    (ops: TranscriptEditOpInput[], reportId?: string | null) =>
      run((baseSeq) =>
        applyTranscriptEdits(episodeId!, { base_seq: baseSeq, session_id: sessionId, ops, report_id: reportId ?? null }),
      ),
    [run, episodeId, sessionId],
  )

  const revert = useCallback(
    async (editId: string) => {
      await run((baseSeq) => revertTranscriptEdit(episodeId!, editId, { base_seq: baseSeq, session_id: sessionId }))
    },
    [run, episodeId, sessionId],
  )

  // Undo / redo work on this session's live rows: the last live non-revert
  // op is undone; the last live revert is redone (reverting the revert).
  const sessionRows = useMemo(
    () => (query.data?.edits ?? []).filter((e) => e.session_id === sessionId),
    [query.data, sessionId],
  )
  const undoTarget = useMemo(
    () => [...sessionRows].reverse().find((e) => e.op !== 'revert' && e.status !== 'reverted') ?? null,
    [sessionRows],
  )
  const redoTarget = useMemo(() => {
    const reverted = new Set((query.data?.edits ?? []).filter((e) => e.status === 'reverted').map((e) => e.id))
    // A live revert is one whose target is still reverted and which no
    // later revert cancelled.
    const cancelled = new Set(
      (query.data?.edits ?? []).filter((e) => e.op === 'revert' && e.status !== 'reverted').map((e) => e.reverts),
    )
    return (
      [...sessionRows]
        .reverse()
        .find((e) => e.op === 'revert' && e.reverts && reverted.has(e.reverts) && !cancelled.has(e.id)) ?? null
    )
  }, [query.data, sessionRows])

  const undo = useCallback(async () => {
    if (undoTarget) await revert(undoTarget.id)
  }, [undoTarget, revert])
  const redo = useCallback(async () => {
    if (redoTarget) await revert(redoTarget.id)
  }, [redoTarget, revert])

  const finish = useCallback(async () => {
    if (!episodeId) return null
    try {
      const result = await finishTranscriptEdits(episodeId, sessionId)
      await invalidate()
      // The refresh rewrites mentions and chunks; the reader polls tasks.
      await queryClient.invalidateQueries({ queryKey: ['episodes', 'tasks', episodeId] })
      return result
    } catch (error) {
      setSaveStatus('error')
      setSaveError(error instanceof Error ? error.message : 'Could not finish')
      return null
    }
  }, [episodeId, sessionId, invalidate, queryClient])

  const saveAsHost = useCallback(
    async (name: string, entityId: string | null) => {
      if (!episodeId) return null
      try {
        const result = await saveSpeakerAsHost(episodeId, { name, entity_id: entityId })
        return result.hosts
      } catch (error) {
        setSaveStatus('error')
        setSaveError(error instanceof Error ? error.message : 'Could not save host')
        return null
      }
    },
    [episodeId],
  )

  const renameLabel = useCallback(
    async (label: string, name: string, entityId: string | null) => {
      await apply([{ op: 'speaker.rename_label', anchor: { label }, args: { name, entity_id: entityId } }])
    },
    [apply],
  )
  const mergeLabels = useCallback(
    async (label: string, intoLabel: string) => {
      await apply([{ op: 'speaker.merge_labels', anchor: { label }, args: { into_label: intoLabel } }])
    },
    [apply],
  )
  const setSegmentSpeaker = useCallback(
    async (segment: AnnotatedSegment, target: { label: string } | { name: string; entityId: string | null }) => {
      const args = 'label' in target ? { label: target.label } : { name: target.name, entity_id: target.entityId }
      await apply([{ op: 'segment.set_speaker', anchor: { ...segmentAnchor(segment) }, args }])
    },
    [apply],
  )
  const setSegmentText = useCallback(
    async (segment: AnnotatedSegment, newText: string) => {
      await apply([
        { op: 'segment.set_text', anchor: { ...segmentAnchor(segment) }, args: { old_text: segment.text, new_text: newText } },
      ])
    },
    [apply],
  )
  const setSegmentKind = useCallback(
    async (segment: AnnotatedSegment, kind: SegmentKind) => {
      await apply([{ op: 'segment.set_kind', anchor: { ...segmentAnchor(segment) }, args: { kind } }])
    },
    [apply],
  )
  const splitSegment = useCallback(
    async (segment: AnnotatedSegment, textWordIndex: number) => {
      await apply([{ op: 'segment.split', anchor: { ...segmentAnchor(segment) }, args: { text_word_index: textWordIndex } }])
    },
    [apply],
  )
  const mergeSegments = useCallback(
    async (first: AnnotatedSegment, second: AnnotatedSegment) => {
      await apply([
        { op: 'segment.merge', anchor: { first: segmentAnchor(first), second: segmentAnchor(second) }, args: {} },
      ])
    },
    [apply],
  )

  return {
    state: query.data,
    isLoading: query.isLoading,
    loadError: query.error as Error | null,
    sessionId,
    saveStatus,
    saveError,
    canUndo: undoTarget !== null,
    canRedo: redoTarget !== null,
    pending: write.isPending,
    apply,
    undo,
    redo,
    revert,
    finish,
    saveAsHost,
    renameLabel,
    mergeLabels,
    setSegmentSpeaker,
    setSegmentText,
    setSegmentKind,
    splitSegment,
    mergeSegments,
  }
}
