import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { usePlayer, usePlayerTime } from '../contexts/PlayerContext'
import { useEpisode, useEpisodeTranscript, useEpisodeTranscriptWords } from '../hooks/useApi'
import { useTranscriptEditor } from '../hooks/useTranscriptEditor'
import { useAutoScrollFollow } from '../hooks/useAutoScrollFollow'
import { useIsSmUp } from '../hooks/useMediaQuery'
import { useToast } from '../components/Toast'
import TranscriptEditorBar from '../components/transcript-editor/TranscriptEditorBar'
import SpeakerPanel from '../components/transcript-editor/SpeakerPanel'
import SegmentInspector from '../components/transcript-editor/SegmentInspector'
import EditorTranscript from '../components/transcript-editor/EditorTranscript'
import TransportDock from '../components/transcript-editor/TransportDock'
import { SKIP_SECONDS, useEditorPlayback } from '../components/transcript-editor/useEditorPlayback'
import LaneTimeline from '../components/transcript-editor/LaneTimeline'
import SpeakerDialog from '../components/transcript-editor/SpeakerDialog'
import ReportProposalBanner from '../components/transcript-editor/ReportProposalBanner'
import { parseReportProposal } from '../components/transcript-editor/reportProposal'
import type { SegmentEditing } from '../components/transcript-editor/segmentEditing'
import type { AnnotatedSegment, SegmentKind, TranscriptSpeaker } from '../api/types'
import { buildPlayerTrack } from '../utils/playerTrack'
import { buildSpeakerColorMap, resolveSpeakerColor } from '../utils/speakerColors'
import { findActiveSegmentIndex } from '../utils/transcriptSearch'
import { HIGHLIGHT_LEAD_SECONDS } from '../utils/highlightLead'

// Spec #110 §5 — the transcript editor as its own screen: no episode
// chrome, the speakers column with the instruments for judging a voice, the
// transcript as turns, and a transport dock with per-voice lanes. The op
// log and the ``useTranscriptEditor`` hook are shared with nothing else;
// this page only decides what to show and which op a gesture writes.

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export default function TranscriptEditorPage() {
  const { podcastSlug, episodeSlug } = useParams<{ podcastSlug: string; episodeSlug: string }>()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { can, isLoading: authLoading } = useAuth()
  const isSmUp = useIsSmUp()
  const { showToast } = useToast()
  const player = usePlayer()
  const currentTime = usePlayerTime()

  const episodePath = `/podcasts/${podcastSlug}/episodes/${episodeSlug}`
  const backTo = `${episodePath}?view=transcript`
  const allowed = can('transcript.edit')

  const { data: episodeData, isLoading: episodeLoading, error: episodeError } = useEpisode(podcastSlug!, episodeSlug!, {
    live: false,
  })
  const episode = episodeData?.episode
  const { data: transcriptData, isLoading: transcriptLoading } = useEpisodeTranscript(podcastSlug!, episodeSlug!)
  const wordsQuery = useEpisodeTranscriptWords(podcastSlug!, episodeSlug!, allowed)
  const slugs = useMemo(
    () => (podcastSlug && episodeSlug ? { podcastSlug, episodeSlug } : undefined),
    [podcastSlug, episodeSlug],
  )
  const editor = useTranscriptEditor(episode?.id, slugs, { enabled: allowed && Boolean(episode) })

  const segments = useMemo(() => transcriptData?.segments?.segments ?? [], [transcriptData])
  const offset = transcriptData?.segments?.playback_time_offset_seconds ?? 0
  const speakers = useMemo(() => editor.state?.speakers ?? [], [editor.state])
  const labelsBySegmentId = useMemo(() => {
    const map = new Map<number, string>()
    for (const speaker of speakers) for (const id of speaker.segment_ids) map.set(id, speaker.label)
    return map
  }, [speakers])
  // The viewer's colour map is keyed by name; lanes and the panel key by
  // label, so a voice keeps the colour the reader's transcript gives it.
  const colors = useMemo(() => {
    const byName = buildSpeakerColorMap(speakers.map((s) => s.name ?? s.label))
    return new Map(speakers.map((s) => [s.label, resolveSpeakerColor(s.name ?? s.label, byName)]))
  }, [speakers])
  const conflictSegmentIds = useMemo(() => {
    // A conflict names a segment by its raw anchors; map those back to rows.
    const out = new Set<number>()
    const byAnchor = new Map(segments.map((s) => [s.source_segment_ids.join(','), s.id]))
    for (const edit of editor.state?.edits ?? []) {
      if (edit.status !== 'conflict') continue
      const ids = (edit.anchor as { source_segment_ids?: number[] }).source_segment_ids
      const id = ids ? byAnchor.get(ids.join(',')) : undefined
      if (id !== undefined) out.add(id)
    }
    return out
  }, [segments, editor.state])

  // --- selection, focus, editing --------------------------------------------
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [focusLabel, setFocusLabel] = useState<string | null>(null)
  const [editingSegmentId, setEditingSegmentId] = useState<number | null>(null)
  const [speakerDialog, setSpeakerDialog] = useState<
    { kind: 'label'; speaker: TranscriptSpeaker } | { kind: 'segment'; segment: AnnotatedSegment } | null
  >(null)
  const [finishing, setFinishing] = useState(false)
  const selected = useMemo(() => segments.find((s) => s.id === selectedId) ?? null, [segments, selectedId])

  // --- playback ----------------------------------------------------------------
  const desiredTrack = useMemo(() => {
    if (!episode) return null
    const track = buildPlayerTrack(episode)
    // Spec #110 §5.6 — play the file the timestamps were made from.
    if (editor.state?.local_audio) {
      return { ...track, audioUrl: `/api/episodes/${episode.id}/transcript/edits/audio`, playback: null }
    }
    return track
  }, [episode, editor.state?.local_audio])
  const {
    isCurrent,
    duration,
    seekTo,
    togglePlay,
    skipBy,
    pause: pausePlayback,
  } = useEditorPlayback({
    desiredTrack,
    player,
    currentTime,
    fallbackStart: selected ? selected.start + offset : 0,
    episodeDuration: episode?.duration ?? 0,
  })
  const activeId = useMemo(() => {
    if (!isCurrent || segments.length === 0) return null
    const index = findActiveSegmentIndex(segments, currentTime + HIGHLIGHT_LEAD_SECONDS, offset)
    return index < 0 ? null : segments[index].id
  }, [isCurrent, segments, currentTime, offset])

  const follow = useAutoScrollFollow({ activeKey: activeId, enabled: isCurrent && player.isPlaying })
  const selectRow = useCallback(
    (id: number, scroll = true) => {
      setSelectedId(id)
      if (scroll) follow.scrollToKey(id)
    },
    [follow],
  )

  // --- speaker instruments (§5.3) ---------------------------------------------
  const playSample = useCallback(
    (speaker: TranscriptSpeaker) => {
      const own = segments.filter((s) => speaker.segment_ids.includes(s.id) && s.kind === 'content')
      const longest = own.reduce<AnnotatedSegment | null>((best, s) => (!best || s.end - s.start > best.end - best.start ? s : best), null)
      if (!longest) return
      selectRow(longest.id)
      seekTo(longest.start + offset)
    },
    [segments, selectRow, seekTo, offset],
  )
  const stepVoice = useCallback(
    (speaker: TranscriptSpeaker, delta: -1 | 1) => {
      const ids = speaker.segment_ids
      if (ids.length === 0) return
      const reference = selectedId !== null && ids.includes(selectedId) ? selectedId : activeId !== null && ids.includes(activeId) ? activeId : null
      let index: number
      if (reference === null) {
        // From nowhere: the first turn after the playhead, or the first turn.
        const after = segments.find((s) => ids.includes(s.id) && s.start + offset >= currentTime)
        index = after ? ids.indexOf(after.id) : 0
      } else {
        index = (ids.indexOf(reference) + delta + ids.length) % ids.length
      }
      const target = segments.find((s) => s.id === ids[index])
      if (!target) return
      selectRow(target.id)
      seekTo(target.start + offset)
    },
    [segments, selectedId, activeId, currentTime, offset, selectRow, seekTo],
  )

  // --- ops ----------------------------------------------------------------------
  const locked = Boolean(editor.state?.busy)
  const disabled = locked || editor.pending
  const mergeWithPrevious = useCallback(
    (segment: AnnotatedSegment) => {
      const index = segments.findIndex((s) => s.id === segment.id)
      const previous = index > 0 ? segments[index - 1] : null
      if (!previous || previous.kind !== segment.kind) {
        showToast('Only two neighbouring segments of the same kind can be merged', 'error')
        return
      }
      void editor.mergeSegments(previous, segment)
    },
    [segments, editor, showToast],
  )
  const editing = useMemo<SegmentEditing>(
    () => ({
      labelsBySegmentId,
      speakers,
      editingSegmentId,
      onEditSegment: setEditingSegmentId,
      onSetSpeaker: (segment, target) => void editor.setSegmentSpeaker(segment, target),
      onPickSpeakerName: (segment) => setSpeakerDialog({ kind: 'segment', segment }),
      onSetText: (segment, text) => void editor.setSegmentText(segment, text),
      onSetKind: (segment, kind: SegmentKind) => void editor.setSegmentKind(segment, kind),
      onSplit: (segment, index) => void editor.splitSegment(segment, index),
      onMergeWithPrevious: mergeWithPrevious,
      disabled,
    }),
    [labelsBySegmentId, speakers, editingSegmentId, editor, mergeWithPrevious, disabled],
  )

  // --- report deep link (Phase 3) ---------------------------------------------
  const reportProposal = useMemo(() => parseReportProposal(searchParams), [searchParams])
  const scrolledReportRef = useRef<string | null>(null)
  useEffect(() => {
    if (!reportProposal || segments.length === 0 || scrolledReportRef.current === reportProposal.reportId) return
    scrolledReportRef.current = reportProposal.reportId
    selectRow(reportProposal.segmentId)
  }, [reportProposal, segments, selectRow])
  const reportSegment = useMemo(
    () => (reportProposal ? segments.find((s) => s.id === reportProposal.segmentId) ?? null : null),
    [reportProposal, segments],
  )
  const dismissReport = useCallback(() => navigate(episodePath + '/edit', { replace: true }), [navigate, episodePath])
  const acceptReport = useCallback(async () => {
    if (!reportProposal || !reportSegment || reportProposal.value === null) return
    const anchor = {
      source_segment_ids: reportSegment.source_segment_ids,
      source_word_span: reportSegment.source_word_span,
      start_s: reportSegment.start,
      end_s: reportSegment.end,
      text: reportSegment.text,
    }
    const op =
      reportProposal.fix === 'wrong_speaker'
        ? { op: 'segment.set_speaker' as const, anchor, args: { name: reportProposal.value, entity_id: null } }
        : reportProposal.fix === 'wrong_words'
          ? { op: 'segment.set_text' as const, anchor, args: { old_text: reportSegment.text, new_text: reportProposal.value } }
          : { op: 'segment.set_kind' as const, anchor, args: { kind: reportProposal.value } }
    const applied = await editor.apply([op], reportProposal.reportId)
    if (applied) dismissReport()
  }, [reportProposal, reportSegment, editor, dismissReport])

  // --- finish, dialogs ----------------------------------------------------------
  const handleFinish = useCallback(async () => {
    setFinishing(true)
    const result = await editor.finish()
    setFinishing(false)
    if (!result) return
    showToast(
      result.refresh === 'queued'
        ? 'Edits saved. People, quotes and search are refreshing.'
        : result.refresh === 'deferred'
          ? 'Edits saved. The refresh runs once the episode is idle.'
          : 'No edits to apply.',
      'success',
    )
    navigate(backTo)
  }, [editor, showToast, navigate, backTo])
  const handleSpeakerDialogConfirm = useCallback(
    async ({ name, entityId }: { name: string; entityId: string | null }) => {
      if (!speakerDialog) return
      if (speakerDialog.kind === 'label') await editor.renameLabel(speakerDialog.speaker.label, name, entityId)
      else await editor.setSegmentSpeaker(speakerDialog.segment, { name, entityId })
      setSpeakerDialog(null)
    },
    [speakerDialog, editor],
  )
  const handleSaveAsHost = useCallback(
    async (speaker: TranscriptSpeaker) => {
      if (!speaker.name) return
      const hosts = await editor.saveAsHost(speaker.name, speaker.entity_id)
      if (hosts) showToast(`${speaker.name} saved as a host of this show`, 'success')
    },
    [editor, showToast],
  )

  // --- keyboard (§5.7) --------------------------------------------------------
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return
      const index = selectedId === null ? -1 : segments.findIndex((s) => s.id === selectedId)
      switch (e.key) {
        case ' ':
          e.preventDefault()
          togglePlay()
          return
        case 'j':
        case 'J':
          skipBy(-SKIP_SECONDS)
          return
        case 'k':
        case 'K':
          pausePlayback()
          return
        case 'l':
        case 'L':
          skipBy(SKIP_SECONDS)
          return
        case 'ArrowDown':
        case 'ArrowUp': {
          e.preventDefault()
          if (segments.length === 0) return
          const next = index === -1 ? 0 : Math.min(segments.length - 1, Math.max(0, index + (e.key === 'ArrowDown' ? 1 : -1)))
          selectRow(segments[next].id)
          return
        }
        case 'Enter':
          if (selected && !disabled) {
            e.preventDefault()
            setEditingSegmentId(selected.id)
          }
          return
        case 'm':
        case 'M':
          if (selected && !disabled) mergeWithPrevious(selected)
          return
        case 'Escape':
          setFocusLabel(null)
          return
        default:
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [segments, selectedId, selected, disabled, togglePlay, skipBy, pausePlayback, selectRow, mergeWithPrevious])

  // --- gates --------------------------------------------------------------------
  if (!authLoading && !allowed) return <Navigate to={backTo} replace />
  if (!isSmUp) return <Navigate to={backTo} replace />
  if (episodeError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-page p-6">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="font-medium text-red-700">Could not load the episode</p>
          <p className="mt-1 text-sm text-red-600">{episodeError.message}</p>
          <Link to={backTo} className="mt-4 inline-block text-primary-600 hover:underline">
            ← Back to the episode
          </Link>
        </div>
      </div>
    )
  }
  const loading = episodeLoading || transcriptLoading || (!editor.state && editor.isLoading)
  if (!episode || loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-page">
        <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-primary-600" />
      </div>
    )
  }
  if (!transcriptData?.segments) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-page p-6">
        <div className="rounded-lg border border-hairline bg-surface p-6 text-center">
          <p className="font-medium text-ink">This transcript cannot be edited</p>
          <p className="mt-1 text-sm text-muted">It has no segmented sidecar; reprocess it from Clean first.</p>
          <Link to={backTo} className="mt-4 inline-block text-primary-600 hover:underline">
            ← Back to the episode
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col bg-page" data-testid="transcript-editor-page">
      <TranscriptEditorBar
        backTo={backTo}
        title={episode.title}
        subtitle={episode.podcast_title}
        saveStatus={editor.saveStatus}
        saveError={editor.saveError}
        canUndo={editor.canUndo}
        canRedo={editor.canRedo}
        busy={editor.pending}
        conflicts={editor.state?.conflicts ?? []}
        edits={editor.state?.edits ?? []}
        locked={locked}
        finishing={finishing}
        onUndo={() => void editor.undo()}
        onRedo={() => void editor.redo()}
        onDropConflict={(editId) => void editor.revert(editId)}
        onFinish={() => void handleFinish()}
      />
      {reportProposal && (
        <div className="px-4 pt-3">
          <ReportProposalBanner
            proposal={reportProposal}
            segmentMissing={reportSegment === null}
            busy={editor.pending}
            onAccept={() => void acceptReport()}
            onDismiss={dismissReport}
          />
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <aside className="w-72 shrink-0 overflow-y-auto border-r border-hairline bg-surface" aria-label="Editor tools">
          <SpeakerPanel
            speakers={speakers}
            colors={colors}
            focusLabel={focusLabel}
            onFocus={setFocusLabel}
            onPlaySample={playSample}
            onStep={stepVoice}
            onRename={(speaker) => setSpeakerDialog({ kind: 'label', speaker })}
            onMerge={(speaker, into) => void editor.mergeLabels(speaker.label, into.label)}
            onSaveAsHost={(speaker) => void handleSaveAsHost(speaker)}
            disabled={disabled}
          />
          <SegmentInspector
            segment={selected}
            label={selected ? labelsBySegmentId.get(selected.id) ?? null : null}
            speakers={speakers}
            offset={offset}
            disabled={disabled}
            onSetSpeaker={(segment, target) => void editor.setSegmentSpeaker(segment, target)}
            onPickSpeakerName={(segment) => setSpeakerDialog({ kind: 'segment', segment })}
            onSetKind={(segment, kind) => void editor.setSegmentKind(segment, kind)}
            onEditText={(segment) => setEditingSegmentId(segment.id)}
          />
        </aside>
        <main className="min-w-0 flex-1 overflow-y-auto px-6 py-4" aria-label="Transcript">
          {editor.state?.overlay_failed && (
            <p role="alert" className="mb-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
              The edit log could not be read; this is the transcript without edits.
            </p>
          )}
          <EditorTranscript
            segments={segments}
            offset={offset}
            colors={colors}
            labelsBySegmentId={labelsBySegmentId}
            editing={editing}
            selectedId={selectedId}
            activeId={activeId}
            focusLabel={focusLabel}
            words={wordsQuery.data ?? null}
            conflictSegmentIds={conflictSegmentIds}
            onSelect={(id) => selectRow(id, false)}
            onSeek={seekTo}
            onMerge={(first, second) => void editor.mergeSegments(first, second)}
            registerRef={follow.registerRef}
          />
        </main>
      </div>
      <footer className="border-t border-hairline bg-surface px-4 py-2" aria-label="Playback">
        <TransportDock
          isPlaying={isCurrent && player.isPlaying}
          isLoading={isCurrent && player.isLoading}
          currentTime={isCurrent ? currentTime : 0}
          duration={duration}
          playbackRate={player.playbackRate}
          availableRates={player.availableRates}
          localAudio={Boolean(editor.state?.local_audio)}
          onToggle={togglePlay}
          onSkip={skipBy}
          onRate={player.setRate}
        />
        <div className="mt-2">
          <LaneTimeline
            speakers={speakers}
            segments={segments}
            labelsBySegmentId={labelsBySegmentId}
            colors={colors}
            offset={offset}
            duration={duration}
            currentTime={isCurrent ? currentTime : 0}
            isPlaying={isCurrent && player.isPlaying}
            selectedId={selectedId}
            focusLabel={focusLabel}
            onSelect={(id) => {
              const segment = segments.find((s) => s.id === id)
              selectRow(id)
              if (segment) seekTo(segment.start + offset)
            }}
            onSeek={seekTo}
          />
        </div>
      </footer>
      <SpeakerDialog
        isOpen={speakerDialog !== null}
        title={speakerDialog?.kind === 'segment' ? 'Who is speaking here?' : 'Name this speaker'}
        episodeId={episode.id}
        initialName={
          speakerDialog?.kind === 'label' && speakerDialog.speaker.name && !/^SPEAKER_\d+$/i.test(speakerDialog.speaker.name)
            ? speakerDialog.speaker.name
            : ''
        }
        initialEntityId={speakerDialog?.kind === 'label' ? speakerDialog.speaker.entity_id : null}
        busy={editor.pending}
        onConfirm={(result) => void handleSpeakerDialogConfirm(result)}
        onCancel={() => setSpeakerDialog(null)}
      />
    </div>
  )
}
