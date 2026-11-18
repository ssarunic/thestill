# components/transcript-editor

## Overview

UI pieces of the spec #110 transcript editor page (`pages/TranscriptEditorPage.tsx`): the top bar with undo/redo and conflicts, the editable transcript, speaker panel and dialog, segment inspector, lane timeline, transport dock, and the report-proposal banner. Edits are ops in a server-side log; the network calls live in `api/transcriptEdits.ts` and the op state in `hooks/useTranscriptEditor.ts`.

## Interface

- `TranscriptEditorBar` (default) — back link, title, save status, undo/redo, conflicts toggle, finish [src/components/transcript-editor/TranscriptEditorBar.tsx:TranscriptEditorBar]
- `EditorTranscript` (default), `EditorTranscriptProps` — transcript rows with inline editing [src/components/transcript-editor/EditorTranscript.tsx:EditorTranscript]
- `SpeakerPicker`, `EditableText` — per-segment speaker picker and inline text editor [src/components/transcript-editor/EditableSegment.tsx:SpeakerPicker]
- `SpeakerPanel` (default) — one row per diarization label: sample, step turns, focus, name/merge/save menu [src/components/transcript-editor/SpeakerPanel.tsx:SpeakerPanel]
- `SpeakerDialog` (default), `SpeakerDialogResult` — name a speaker or link an entity (search plus free text) [src/components/transcript-editor/SpeakerDialog.tsx:SpeakerDialog]
- `SegmentInspector` (default) — selected row: speaker, kind, start/end (read-only), Edit text, Hide [src/components/transcript-editor/SegmentInspector.tsx:SegmentInspector]
- `LaneTimeline` (default) — per-speaker lanes with pan/zoom [src/components/transcript-editor/LaneTimeline.tsx:LaneTimeline]
- `TransportDock` (default) — play/skip/rate bound to the app player [src/components/transcript-editor/TransportDock.tsx:TransportDock]
- `ConflictsList` (default) — ops a re-clean / re-transcribe broke, with Drop [src/components/transcript-editor/ConflictsList.tsx:ConflictsList]
- `ReportProposalBanner` (default) — stage a fix proposed by a report deep link [src/components/transcript-editor/ReportProposalBanner.tsx:ReportProposalBanner]
- `useEditorPlayback`, `SKIP_SECONDS`, `EditorPlayback*` — the editor's single playback source [src/components/transcript-editor/useEditorPlayback.ts:useEditorPlayback]
- `parseReportProposal`, `ReportProposal`, `ReportFix` — parse the report deep link [src/components/transcript-editor/reportProposal.ts:parseReportProposal]
- `SEGMENT_KINDS`, `SegmentEditing`, `wordIndexAtCaret`, `isUnnamed` — editing contract and pure helpers [src/components/transcript-editor/segmentEditing.ts:SegmentEditing]
- `tickStep`, `timeToX`, `xToTime`, `clampWindow`, `followWindow`, `zoomWindow`, `ticksFor`, `followDecision`, `MIN_SPAN_SECONDS`, `DEFAULT_SPAN_SECONDS` — lane window math [src/components/transcript-editor/laneMath.ts:followDecision]
- `describeOp` — human text for an op [src/components/transcript-editor/describeOp.ts:describeOp]; `formatClock` [src/components/transcript-editor/time.ts:formatClock]

## Invariants

- A broken op is listed, never dropped; the admin re-applies it by hand or drops it (a revert row) [src/components/transcript-editor/ConflictsList.tsx:ConflictsList]
- Every transport gesture acts on the edited episode on the file its timestamps came from; a same-episode YouTube engine is switched back to `audio` before playing [src/components/transcript-editor/useEditorPlayback.ts:playOurSource]
- `pause` never starts anything: K on a foreign episode leaves it alone [src/components/transcript-editor/useEditorPlayback.ts:useEditorPlayback]
- Report deep-link contract `?view=transcript&edit=1&report=<id>&segment=<id>&fix=wrong_speaker|wrong_words|wrong_kind[&value=…]`; anything else parses to `null` [src/components/transcript-editor/reportProposal.ts:parseReportProposal]
- Hide sets kind `filler` (the non-destructive delete); start/end are read-only [src/components/transcript-editor/SegmentInspector.tsx:SegmentInspector]
- Only one segment's text is open for editing; writes are refused while `disabled` (busy pipeline or save in flight) [src/components/transcript-editor/segmentEditing.ts:SegmentEditing]
- After a manual pan/zoom the timeline holds until the playhead leaves the chosen window; it never snaps back under the user [src/components/transcript-editor/laneMath.ts:followDecision]
- Speaker focus dims other speakers, never filters [src/components/transcript-editor/SpeakerPanel.tsx:SpeakerPanel]
- ⌘Z / ⇧⌘Z are ignored inside text fields (a textarea keeps its own undo) [src/components/transcript-editor/TranscriptEditorBar.tsx:TranscriptEditorBar]

## Side Effects

- `GET /api/episodes/{id}/transcript/edits/people?q=` (debounced 150 ms, aborted on change, queries under 2 chars skipped) [src/components/transcript-editor/SpeakerDialog.tsx:SpeakerDialog]
- Window `keydown` listener for undo/redo [src/components/transcript-editor/TranscriptEditorBar.tsx:TranscriptEditorBar]
- Drives the global player (`play`, `seek`, `resume`, `toggle`, `skip`, `switchRendition`) [src/components/transcript-editor/useEditorPlayback.ts:useEditorPlayback]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
